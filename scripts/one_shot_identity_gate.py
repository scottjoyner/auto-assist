#!/usr/bin/env python3
"""Default-HOLD, source-pinned, never-worker launcher for a supervised node.

Run ONLY from a privately staged bundle; this cannot authorize Task execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from urllib.parse import urlsplit

PINNED = {
    "fleet_node_agent.py": "02d7ea9f9ca637376a810ad0f537e8cf2b0e200090a7e506bbb215eafe5057b0",
    "lm_studio_client_auth.py": "12a1a444948b0c115a455f2664d88b2feea09977ea75b20af1d795a213c29467",
    "recovery_runbooks.py": "c946869ab6ba3c9907879e7bbb15a5f5d77abe2a331ed1b915c640e8e4623859",
}
STAGE_COMMIT = "63ce16f02d1a11be347f1cd83162e7dcf0183494"


def _private_stage(path: Path) -> bool:
    try:
        mode = path.stat()
        return (path.is_dir() and not path.is_symlink()
                and mode.st_uid == os.getuid()
                and not stat.S_IMODE(mode.st_mode) & 0o077)
    except OSError:
        return False


def _verify(stage: Path) -> bool:
    """Fail closed before evaluating any credential or making an HTTP call."""
    if not _private_stage(stage):
        return False
    root = stage / "src"
    package = root / "assistx"
    for directory in (root, package):
        try:
            meta = directory.stat()
            if (not directory.is_dir() or directory.is_symlink()
                    or meta.st_uid != os.getuid()
                    or stat.S_IMODE(meta.st_mode) & 0o022):
                return False
        except OSError:
            return False
    # An unchecked __init__, sidecar, or pyc could run code before the pinned
    # agent module. The staging bundle has exactly three sources and empty init.
    try:
        if {p.name for p in root.iterdir()} != {"assistx"}:
            return False
        if {p.name for p in package.iterdir()} != set(PINNED) | {"__init__.py"}:
            return False
        initializer = package / "__init__.py"
        init_meta = initializer.stat()
        if (initializer.is_symlink() or not initializer.is_file()
                or init_meta.st_uid != os.getuid()
                or stat.S_IMODE(init_meta.st_mode) & 0o022
                or initializer.read_bytes() != b""):
            return False
    except OSError:
        return False
    for filename, expected in PINNED.items():
        candidate = stage / "src" / "assistx" / filename
        try:
            if candidate.is_symlink() or not candidate.is_file():
                return False
            info = candidate.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
                return False
            with candidate.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
                    return False
        except OSError:
            return False
    return True


def _approved_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        port = parsed.port
        if not parsed.hostname or parsed.username or parsed.password or port == 0:
            return False
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            return False
        return (parsed.scheme == "https"
                or (parsed.scheme == "http"
                    and parsed.hostname in {"127.0.0.1", "::1"}))
    except (ValueError, TypeError):
        return False


def _report(reason: str, *, source_verified: bool,
            identity_verified: bool = False) -> int:
    allowed = {"SOURCE_HASH_OR_CUSTODY_MISMATCH", "SOURCE_PINNED_NO_ADMISSION",
               "NODE_IDENTITY_NOT_PROVISIONED", "ORIGIN_NOT_APPROVED",
               "IDENTITY_ONLY_NO_TASK_AUTHORITY", "IDENTITY_NOT_VERIFIED"}
    assert reason in allowed
    print(json.dumps({"contract": "fleet-staging-gate-v1", "source_verified": source_verified,
                      "identity_verified": identity_verified, "task_admission": "HOLD",
                      "production_admission_permitted": False, "reason": reason}, sort_keys=True))
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-only", action="store_true",
                        help="probe only an operator-pinned identity origin; always exit 2")
    args = parser.parse_args(argv)
    stage = Path(__file__).absolute().parent
    if not _verify(stage):
        return _report("SOURCE_HASH_OR_CUSTODY_MISMATCH", source_verified=False)
    if not args.identity_only:
        return _report("SOURCE_PINNED_NO_ADMISSION", source_verified=True)
    node = os.environ.get("FLEET_NODE_ID", "")
    token = os.environ.get("FLEET_NODE_TOKEN", "")
    origin = os.environ.get("FLEET_NODE_IDENTITY_APPROVED_ORIGIN", "")
    if not node or not token:
        return _report("NODE_IDENTITY_NOT_PROVISIONED", source_verified=True)
    if not _approved_origin(origin):
        return _report("ORIGIN_NOT_APPROVED", source_verified=True)
    # Nothing else from the calling session crosses the boundary: no Basic auth,
    # model credentials, inherited Python path, proxy settings or provider keys.
    child_env = {"HOME": str(Path.home()), "PATH": os.defpath,
                 "PYTHONPATH": str(stage / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                 "PYTHONNOUSERSITE": "1", "FLEET_NODE_ID": node,
                 "FLEET_NODE_TOKEN": token,
                 "FLEET_NODE_IDENTITY_APPROVED_ORIGIN": origin}
    try:
        child = subprocess.run(
            [sys.executable, "-s", "-m", "assistx.fleet_node_agent",
             "--identity-probe-only", "--assistx-url", origin],
            env=child_env, text=True, capture_output=True, timeout=8, check=False)
        payload = json.loads(child.stdout)
        success = (child.returncode == 2 and isinstance(payload, dict)
                   and payload.get("identity_verified") is True
                   and payload.get("reason") == "IDENTITY_ONLY_NO_TASK_AUTHORITY"
                   and payload.get("task_admission") == "HOLD"
                   and payload.get("production_admission_permitted") is False
                   and payload.get("legacy_task_authorized") is False)
    except (OSError, ValueError, subprocess.SubprocessError):
        success = False
    return _report("IDENTITY_ONLY_NO_TASK_AUTHORITY" if success else "IDENTITY_NOT_VERIFIED",
                   source_verified=True, identity_verified=success)


if __name__ == "__main__":
    raise SystemExit(main())
