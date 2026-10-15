#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {url} returned HTTP {exc.code}: {body[:500]}") from exc
    except Exception as exc:
        raise RuntimeError(f"GET {url} failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"GET {url} did not return a JSON object")
    return payload


def _sha256_json(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_uint(value: Any, *, positive: bool = False) -> int | None:
    """Accept JSON integer scalars only, without bool/string coercion."""
    if type(value) is not int or not (0 <= value <= (2**63 - 1)):
        return None
    return value if (not positive or value > 0) else None


def _safe_checksum(value: Any) -> str | None:
    return (
        value if isinstance(value, str) and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value) else None
    )


def _revision_digest(value: Any) -> str | None:
    # Revisions can be arbitrary strings (up to 300 chars) in Router's
    # schema. Never put their raw bytes into operator evidence.
    if not isinstance(value, str) or not (1 <= len(value) <= 300):
        return None
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return None


def _identity(value: Any, *, label: str, failures: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        failures.append(f"{label}_object_invalid")
        value = {}
    generation = _safe_uint(value.get("generation"), positive=True)
    revision_sha256 = _revision_digest(value.get("revision"))
    checksum = _safe_checksum(value.get("checksum"))
    for name, field in (
        ("generation", generation),
        ("revision", revision_sha256),
        ("checksum", checksum),
    ):
        if field is None:
            failures.append(f"{label}_{name}_invalid")
    return {
        "generation": generation,
        "revision_sha256": revision_sha256,
        "checksum": checksum,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify AssistX and auto-router agree on the signed runtime projection."
    )
    parser.add_argument("--assistx-url", default="http://127.0.0.1:18000")
    parser.add_argument("--router-url", default="http://127.0.0.1:18088")
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("artifacts/runtime-projection-evidence.json"),
    )
    args = parser.parse_args()

    user = os.getenv("BASIC_AUTH_USER", "").strip()
    password = os.getenv("BASIC_AUTH_PASS", "").strip()
    admin_token = os.getenv("AUTO_ROUTER_ADMIN_TOKEN", "").strip()
    if not user or not password or not admin_token:
        print(
            "RUNTIME_PROJECTION_GATE: BLOCKED missing BASIC_AUTH_USER, "
            "BASIC_AUTH_PASS, or AUTO_ROUTER_ADMIN_TOKEN",
            file=sys.stderr,
        )
        return 2

    basic = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    try:
        assistx = _get_json(
            f"{args.assistx_url.rstrip('/')}/api/router/runtime-projection",
            {"Authorization": f"Basic {basic}", "Accept": "application/json"},
        )
        router = _get_json(
            f"{args.router_url.rstrip('/')}/admin/runtime-projection",
            {"X-Admin-Token": admin_token, "Accept": "application/json"},
        )
    except RuntimeError as exc:
        print(f"RUNTIME_PROJECTION_GATE: BLOCKED {exc}", file=sys.stderr)
        return 1

    failures: list[str] = []
    if assistx.get("source") != "assistx":
        failures.append("assistx_source_invalid")
    if router.get("configured") is not True:
        failures.append("router_projection_unconfigured")
    source_identity = _identity(assistx, label="assistx", failures=failures)
    current_raw = router.get("current")
    target_identity = _identity(current_raw, label="router", failures=failures)
    current = current_raw if isinstance(current_raw, dict) else {}
    for key in ("generation", "checksum", "revision_sha256"):
        left, right = source_identity[key], target_identity[key]
        if left is not None and right is not None and left != right:
            failures.append(f"projection_{key}_mismatch")
    expires = _safe_uint(assistx.get("expires_at_ms"), positive=True)
    if expires is None:
        failures.append("assistx_expiry_invalid")
    providers = assistx.get("providers")
    if not isinstance(providers, list) or not providers:
        failures.append("assistx_provider_inventory_missing")
    provider_count = min(len(providers), 1024) if isinstance(providers, list) else 0
    if isinstance(providers, list) and len(providers) > 1024:
        failures.append("assistx_provider_count_excessive")
    raw_admission = current.get("admission")
    if not isinstance(raw_admission, list) or not raw_admission:
        failures.append("router_admission_missing")
    admission_summary = []
    if isinstance(raw_admission, list):
        if len(raw_admission) > 1024:
            failures.append("router_admission_count_excessive")
        for item in raw_admission[:1024]:
            slots = _safe_uint(item.get("parallel_slots"), positive=True) if isinstance(item, dict) else None
            if slots is None:
                failures.append("router_admission_capacity_invalid")
            admission_summary.append({"parallel_slots": slots})
    if router.get("last_error"):
        failures.append("router_projection_reported_error")
    raw_retired = router.get("retired_generations") or []
    if not isinstance(raw_retired, list):
        failures.append("router_retired_list_invalid")
        raw_retired = []
    if len(raw_retired) > 16:
        failures.append("router_retired_count_excessive")
    retired_summary = [
        _identity(item, label="retired", failures=failures)
        for item in raw_retired[:16]
    ]

    # Evidence intentionally retains its top-level schema and digest, but
    # never serializes arbitrary provider, path, error or runtime-ID text.
    evidence = {
        "schema_version": 1,
        "verified_at_ts": int(time.time() * 1000),
        "status": "pass" if not failures else "blocked",
        "failures": failures,
        "assistx_projection": {
            **source_identity,
            "expires_at_ms": expires,
            "provider_count": provider_count,
        },
        "router_projection": {
            **target_identity,
            "admission": admission_summary,
            "admission_count": len(admission_summary),
        },
        "retired_generations": retired_summary,
    }
    evidence["evidence_sha256"] = _sha256_json(evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if failures:
        print("RUNTIME_PROJECTION_GATE: BLOCKED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(
        "RUNTIME_PROJECTION_GATE: PASS "
        f"generation={assistx.get('generation')} checksum={assistx.get('checksum')}"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
