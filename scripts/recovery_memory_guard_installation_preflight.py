#!/usr/bin/env python3
"""Read-only recovery-memory-guard installation identity gate.

Not a deployment, restart, memory-pressure evaluation or execution authority.
Even a successful source SHA and service-unit match returns HOLD (exit 2).
Never reads live recovery state, service credentials or model data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat

UNIT = Path("/etc/systemd/system/assistx-recovery-memory-guard.service")
ROOT = Path("/srv/assistx-recovery/packages/auto-assist")
ENTRYPOINT = Path("scripts/recovery-memory-guard.py")
MAX_UNIT_BYTES = 64 * 1024
MAX_SCRIPT_BYTES = 128 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class IdentityHold(Exception):
    """Redacted refusal. Never include paths, contents or OS error strings."""


def _read_regular(path: Path, limit: int) -> bytes:
    """Read bounded bytes without following links or blocking on FIFOs."""
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        raise IdentityHold("file_absent_or_inaccessible") from None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise IdentityHold("file_not_regular_or_bounded")
        if before.st_mode & 0o022:
            raise IdentityHold("file_writable_by_group_or_other")
        data = os.read(fd, limit + 1)
        after = os.fstat(fd)
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size,
                               st.st_mtime_ns, st.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(after):
            raise IdentityHold("file_changed_while_reading")
        return data
    finally:
        os.close(fd)


def _reject_symlink_components(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise IdentityHold("path_not_absolute_or_normalized")
    for component in (path, *path.parents):
        try:
            mode = component.lstat().st_mode
        except OSError:
            raise IdentityHold("path_absent_or_inaccessible") from None
        if stat.S_ISLNK(mode):
            raise IdentityHold("symlink_component")


def _service_values(unit_bytes: bytes) -> dict[str, str]:
    try:
        source = unit_bytes.decode("utf-8")
    except UnicodeError:
        raise IdentityHold("unit_invalid_encoding") from None
    section = ""
    values: dict[str, str] = {}
    for line in source.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line
            continue
        if section != "[Service]" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in ("User", "WorkingDirectory", "ExecStart"):
            if key in values:
                raise IdentityHold("ambiguous_service_unit")
            values[key] = value.strip()
    if any(not values.get(key) for key in ("User", "WorkingDirectory", "ExecStart")):
        raise IdentityHold("incomplete_service_unit")
    return values


def inspect(unit_path: Path, package_root: Path, expected_sha256: str) -> dict[str, object]:
    """Source-identity check only; unconditional non-release result."""
    verdict: dict[str, object] = {
        "schema": "beelink-recovery-guard-installation-preflight/v1",
        "decision": "HOLD",
        "entrypoint_identity_matched": False,
        "runtime_guard_evaluated": False,
        "recovery_service_started": False,
        "deployment_authorized": False,
        "model_calls": 0,
        "nas_payload_reads": 0,
    }
    try:
        if not SHA256.fullmatch(expected_sha256):
            raise IdentityHold("invalid_expected_digest")
        if not isinstance(package_root, Path) or not package_root.is_absolute():
            raise IdentityHold("invalid_package_root")
        script = package_root / ENTRYPOINT
        # No symlink parent, even if the file itself is a regular file.
        _reject_symlink_components(unit_path)
        _reject_symlink_components(script)
        values = _service_values(_read_regular(unit_path, MAX_UNIT_BYTES))
        if values["User"] != "assistx-recovery":
            raise IdentityHold("unexpected_service_user")
        if values["WorkingDirectory"] != str(package_root):
            raise IdentityHold("unexpected_working_directory")
        try:
            argv = shlex.split(values["ExecStart"])
        except ValueError:
            raise IdentityHold("invalid_exec_start") from None
        if (len(argv) != 2 or not Path(argv[0]).is_absolute()
                or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(argv[0]).name)
                or argv[1] != str(script)):
            raise IdentityHold("unexpected_exec_start")
        actual = hashlib.sha256(_read_regular(script, MAX_SCRIPT_BYTES)).hexdigest()
        if actual != expected_sha256:
            raise IdentityHold("entrypoint_digest_mismatch")
    except IdentityHold as exc:
        verdict["reason"] = str(exc)
        return verdict
    verdict["entrypoint_identity_matched"] = True
    verdict["reason"] = "file_identity_only_runtime_acceptance_not_verified"
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only, never-authorizing installation preflight")
    parser.add_argument("--unit-file", type=Path, default=UNIT)
    parser.add_argument("--package-root", type=Path, default=ROOT)
    parser.add_argument("--expected-entrypoint-sha256", required=True)
    args = parser.parse_args()
    outcome = inspect(args.unit_file, args.package_root, args.expected_entrypoint_sha256)
    print(json.dumps(outcome, sort_keys=True))
    return 2  # Even exact file identity cannot authorize starting the service.


if __name__ == "__main__":
    raise SystemExit(main())
