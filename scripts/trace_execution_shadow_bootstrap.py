#!/usr/bin/env python3
"""Generate unique node-scoped shadow audit encryption and anchor keys locally.

Never writes any secrets to NAS, SSH targets, Git or stdout. Idempotent:
existing secure key files are checked, never replaced.
"""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from trace_execution_shadow_backup import (
    DEFAULT_ANCHORS,
    DEFAULT_PRIVATE,
    ensure_private_dir,
    secret_file,
)
from trace_execution_shadow_control import load_paths


def private_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    ensure_private_dir(path)


def new_key(path: Path) -> bool:
    if path.exists() or path.is_symlink():
        secret_file(path)
        return False
    # Text is safe in GnuPG passphrase-file and HMAC, 48 random bytes encoded
    # in 96 hex characters. The two node roles never share credentials.
    encoded = (secrets.token_hex(48) + "\n").encode("ascii")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, encoded) != len(encoded):
            raise RuntimeError("short_key_write")
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--private", type=Path, default=DEFAULT_PRIVATE)
    p.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    args = p.parse_args()
    paths = load_paths(args.config)
    private_dir(args.private)
    private_dir(args.anchors)
    created = 0
    for node_id in sorted(paths):
        node_private = args.private / node_id
        node_anchor = args.anchors / node_id
        private_dir(node_private)
        private_dir(node_anchor)
        created += int(new_key(node_private / "encryption.passphrase"))
        created += int(new_key(node_private / "anchor.key"))
    print(f"shadow custody initialized: nodes={len(paths)} new_secret_files={created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
