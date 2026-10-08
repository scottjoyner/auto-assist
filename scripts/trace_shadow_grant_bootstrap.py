#!/usr/bin/env python3
"""Generate per-node shadow-only Ed25519 keys on controller, never overwriting."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from trace_execution_shadow_backup import DEFAULT_PRIVATE, ensure_private_dir
from trace_execution_shadow_control import load_paths

from assistx.trace_shadow_grant import private_key, public_key


def create_once(filename: Path, value: bytes, mode: int) -> bool:
    if filename.exists() or filename.is_symlink():
        return False
    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        if os.write(fd, value) != len(value):
            raise RuntimeError("short_key_write")
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def provision(config: str, private: Path) -> int:
    ensure_private_dir(private)
    count = 0
    for node in sorted(load_paths(config)):
        directory = private / node
        if not directory.exists() and not directory.is_symlink():
            directory.mkdir(mode=0o700)
        ensure_private_dir(directory)
        signer = directory / "grant-signing-key.pem"
        public = directory / "grant-public-key.pem"
        if signer.exists() or signer.is_symlink():
            key = private_key(signer)
        else:
            key = Ed25519PrivateKey.generate()
            encoded = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            count += int(create_once(signer, encoded, 0o600))
        public_encoded = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        if public.exists() or public.is_symlink():
            stored = public_key(public).public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            if stored != public_encoded:
                raise RuntimeError("shadow_public_key_mismatch")
        else:
            count += int(create_once(public, public_encoded, 0o600))
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--private", type=Path, default=DEFAULT_PRIVATE)
    args = parser.parse_args()
    print("node-scoped shadow keys created:", provision(args.config, args.private))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
