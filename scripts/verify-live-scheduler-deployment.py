#!/usr/bin/env python3
"""Read-only proof that deployed scheduler bytes match reviewed repository code.

This checks code identity, not authorization: a matching deployment does NOT
renew canonical projection approval or admit a single inference provider.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROBE = r"""
import hashlib
import importlib
import json
from pathlib import Path
import sys

name = sys.argv[1]
module = importlib.import_module(name)
path = Path(module.__file__).resolve()
raw = path.read_bytes()
print(json.dumps({
    "module": name,
    "sha256": hashlib.sha256(raw).hexdigest(),
    "mtime_ns": path.stat().st_mtime_ns,
    "module_path": str(path),
}))
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(
    name: str,
    expected_sha256: str,
    deployed: dict[str, Any] | None,
    *,
    started_ns: int | None = None,
    must_predate_start: bool = False,
) -> dict[str, Any]:
    """Return evidence-only comparison, fail closed on missing or stale proof."""
    out: dict[str, Any] = {
        "component": name,
        "status": "blocked",
        "expected_sha256": expected_sha256,
        "deployed_sha256": None,
        "reason": "missing_live_probe",
        "admission_applied": False,
        "authorized_capacity": "not_evaluated",
    }
    if not isinstance(deployed, dict):
        return out
    digest = deployed.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        out["reason"] = "invalid_live_digest"
        return out
    out["deployed_sha256"] = digest
    if digest != expected_sha256:
        out["reason"] = "deployment_source_drift"
        return out
    if must_predate_start:
        mtime = deployed.get("mtime_ns")
        if not isinstance(started_ns, int) or not isinstance(mtime, int):
            out["reason"] = "missing_process_freshness"
            return out
        # Bind-mounted source files changed after start require an actual service
        # restart; byte equality in a second Python process is not enough.
        if mtime > started_ns + 2_000_000_000:
            out["reason"] = "service_started_before_source"
            return out
    out["status"] = "code_matches"
    out["reason"] = "source_identity_verified; signed_admission_still_required"
    return out


def _run_json(command: list[str], timeout: float) -> Any:
    response = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if response.returncode != 0:
        raise RuntimeError("read-only container inspection failed")
    return json.loads(response.stdout)


def _container_started_ns(container: str, timeout: float) -> int:
    inspect = _run_json(["docker", "inspect", container], timeout)
    if not isinstance(inspect, list) or len(inspect) != 1:
        raise ValueError("unexpected container metadata")
    value = inspect[0].get("State", {}).get("StartedAt")
    if not isinstance(value, str):
        raise ValueError("container startup timestamp unavailable")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("container startup timestamp lacks timezone")
    return int(date.timestamp() * 1_000_000_000)


def _probe_container(container: str, module: str, timeout: float) -> dict:
    result = _run_json(
        ["docker", "exec", container, "python", "-c", PROBE, module],
        timeout,
    )
    if not isinstance(result, dict) or result.get("module") != module:
        raise ValueError("container probe returned wrong module")
    return result


def verify(
    assistx_source: Path,
    router_source: Path,
    *,
    assistx_container: str = "assistx-api",
    router_container: str = "git-router-1",
    timeout: float = 7,
) -> dict:
    expected = [
        ("assistx_work_supply", assistx_source, assistx_container, "assistx.work_supply", True),
        ("auto_router_backlog", router_source, router_container, "auto_router.backlog_scheduler", False),
    ]
    evidence = []
    for name, path, container, module, bound_source in expected:
        if not path.is_file() or path.is_symlink():
            evidence.append({
                "component": name,
                "status": "blocked",
                "reason": "reviewed_source_missing_or_symlink",
                "admission_applied": False,
                "authorized_capacity": "not_evaluated",
            })
            continue
        digest = sha256(path)
        try:
            live = _probe_container(container, module, timeout)
            started = _container_started_ns(container, timeout) if bound_source else None
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            live = None
            started = None
        evidence.append(evaluate(
            name, digest, live, started_ns=started, must_predate_start=bound_source,
        ))
    passed = all(row["status"] == "code_matches" for row in evidence)
    return {
        "schema_version": 1,
        "status": "code_matches" if passed else "blocked",
        "components": evidence,
        "admission_applied": False,
        "production_inference_admitted": "not_evaluated",
        "note": "Code identity does not satisfy runtime, model, capacity or signer admission evidence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_assistx = Path(__file__).resolve().parents[1] / "src/assistx/work_supply.py"
    default_router = default_assistx.parents[3] / "auto-router/src/auto_router/backlog_scheduler.py"
    parser.add_argument("--assistx-source", type=Path, default=default_assistx)
    parser.add_argument("--router-source", type=Path, default=default_router)
    parser.add_argument("--assistx-container", default="assistx-api")
    parser.add_argument("--router-container", default="git-router-1")
    parser.add_argument("--timeout", type=float, default=7)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 20:
        parser.error("timeout must be between 1 and 20 seconds")
    for container in (args.assistx_container, args.router_container):
        if not container or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in container):
            parser.error("container names must be plain names")
    report = verify(
        args.assistx_source,
        args.router_source,
        assistx_container=args.assistx_container,
        router_container=args.router_container,
        timeout=args.timeout,
    )
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["status"] == "code_matches" else 2


if __name__ == "__main__":
    sys.exit(main())
