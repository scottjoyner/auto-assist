#!/usr/bin/env python3
"""Read-only Linux GGUF process continuity witness; never signs or authorizes.

Run on the *physical source host*, not on the router, not through a localhost
proxy. Records Linux boot UUID + /proc PID start ticks, exact GGUF file digest,
and checks continuity across hashing. Output is unsigned and non-actionable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import time
import uuid
from pathlib import Path

MAX_MODEL_BYTES = 2 * 1024**3
MAX_CMDLINE_BYTES = 16_384
HASH = re.compile(r"[0-9a-f]{64}\Z")


def argument(argv: list[str], names: tuple[str, ...]) -> str | None:
    values = []
    for idx, val in enumerate(argv):
        for name in names:
            if val == name:
                if idx + 1 < len(argv):
                    values.append(argv[idx + 1])
                else:
                    return None
            if val.startswith(name + "="):
                values.append(val[len(name) + 1:])
    return values[0] if len(values) == 1 else None


def process_start_ticks(value: str) -> int:
    # /proc/PID/stat field 2 (comm) can contain spaces and parentheses.
    # Parse from the LAST right parenthesis; field 3 begins after ') '.
    close = value.rfind(") ")
    if close < 2:
        raise ValueError("malformed Linux process stat")
    remainder = value[close + 2:].split()
    if len(remainder) < 20:
        raise ValueError("incomplete Linux process stat")
    ticks = int(remainder[19])  # field 22, 0-indexed from field 3
    if ticks <= 0:
        raise ValueError("invalid Linux process start ticks")
    return ticks


def read_process_identity(proc_root: Path, pid: int) -> tuple[int, bytes, str]:
    process = proc_root / str(pid)
    args_bytes = (process / "cmdline").read_bytes()
    if not 0 < len(args_bytes) <= MAX_CMDLINE_BYTES:
        raise ValueError("unbounded or empty process argv")
    args = [x.decode("utf-8", errors="strict")
            for x in args_bytes.split(b"\0") if x]
    if not args or Path(args[0]).name != "llama-server":
        raise ValueError("PID is not a llama-server process")
    ticks = process_start_ticks((process / "stat").read_text())
    model = argument(args, ("-m", "--model"))
    if model is None:
        raise ValueError("exactly one explicit model path required")
    return ticks, args_bytes, model


def collect(
    *, pid: int, expected_digest: str, expected_bytes: int,
    proc_root: Path = Path("/proc"),
    boot_id_file: Path = Path("/proc/sys/kernel/random/boot_id"),
) -> dict:
    if type(pid) is not int or not 1 <= pid < 2**31:
        raise ValueError("invalid PID")
    if not isinstance(expected_digest, str) or not HASH.fullmatch(expected_digest):
        raise ValueError("invalid lower-case GGUF SHA256")
    if type(expected_bytes) is not int or not 0 < expected_bytes <= MAX_MODEL_BYTES:
        raise ValueError("invalid bounded GGUF size")

    boot_a = str(uuid.UUID(boot_id_file.read_text().strip()))
    ticks_a, argv_a, raw_path = read_process_identity(proc_root, pid)
    model = Path(raw_path)
    if not model.is_absolute() or model.is_symlink():
        raise ValueError("model path must be absolute and non-symlink")
    before = model.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size != expected_bytes:
        raise ValueError("model size or type mismatch")
    with model.open("rb") as reader:
        digest = hashlib.file_digest(reader, "sha256").hexdigest()
    after = model.stat()
    if digest != expected_digest:
        raise ValueError("GGUF checksum does not match independently pinned digest")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise ValueError("model changed during hashing")
    ticks_b, argv_b, model_b = read_process_identity(proc_root, pid)
    boot_b = str(uuid.UUID(boot_id_file.read_text().strip()))
    if (boot_a, ticks_a, argv_a, raw_path) != (
        boot_b, ticks_b, argv_b, model_b
    ):
        raise ValueError("boot or process identity changed during observation")
    return {
        "schema": "linux-gguf-process-continuity-v1",
        "source_kind": "unsigned_host_local_proc",
        "observed_at_ms": int(time.time() * 1000),
        "pid": pid,
        "boot_id": boot_a,
        "process_start_ticks": ticks_a,
        "model_artifact_fingerprint": "sha256:" + digest,
        "model_bytes": before.st_size,
        "continuity_checked": True,
        "signature_verified": False,
        "model_loaded_attested": False,
        "owner_bound": False,
        "can_approve": False,
        "can_dispatch": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--bytes", type=int, required=True)
    args = parser.parse_args()
    try:
        data = collect(
            pid=args.pid, expected_digest=args.sha256,
            expected_bytes=args.bytes,
        )
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        print("host process witness refused: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(data, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
