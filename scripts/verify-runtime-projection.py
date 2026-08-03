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

    current = router.get("current") if isinstance(router.get("current"), dict) else {}
    failures: list[str] = []
    if assistx.get("source") != "assistx":
        failures.append("AssistX projection source is not canonical")
    if not router.get("configured"):
        failures.append("auto-router has not applied a live runtime projection")
    if int(assistx.get("generation") or 0) <= 0:
        failures.append("AssistX projection generation is invalid")
    if int(current.get("generation") or 0) != int(assistx.get("generation") or 0):
        failures.append("generation mismatch between AssistX and auto-router")
    if str(current.get("checksum") or "") != str(assistx.get("checksum") or ""):
        failures.append("checksum mismatch between AssistX and auto-router")
    if str(current.get("revision") or "") != str(assistx.get("revision") or ""):
        failures.append("revision mismatch between AssistX and auto-router")
    providers = assistx.get("providers") if isinstance(assistx.get("providers"), list) else []
    if not providers:
        failures.append("AssistX projection contains no providers")
    admission = current.get("admission") if isinstance(current.get("admission"), list) else []
    if not admission:
        failures.append("auto-router projection generation contains no admission gates")
    for item in admission:
        if int(item.get("parallel_slots") or 0) <= 0:
            failures.append(
                f"runtime {item.get('runtime_instance_id')} has zero projected capacity"
            )
    if router.get("last_error"):
        failures.append(f"auto-router projection error: {router.get('last_error')}")

    evidence = {
        "schema_version": 1,
        "verified_at_ts": int(time.time() * 1000),
        "status": "pass" if not failures else "blocked",
        "failures": failures,
        "assistx_projection": {
            "generation": assistx.get("generation"),
            "revision": assistx.get("revision"),
            "checksum": assistx.get("checksum"),
            "expires_at_ms": assistx.get("expires_at_ms"),
            "provider_count": len(providers),
        },
        "router_projection": current,
        "retired_generations": router.get("retired_generations") or [],
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
