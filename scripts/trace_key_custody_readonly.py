#!/usr/bin/env python3
"""Metadata-only custody check for trace issuer and node verifier key paths.

No signing key content is opened, read, copied, logged, or hashed. Public
verifier files may be hashed for pin checks. Never grants production authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

MAX_KEY_BYTES = 16_384


def verify_metadata(path, *, private, expected_public_sha256=None):
    if not path:
        return {"state": "not_configured", "metadata_acceptable": False,
                "public_pin_verified": False}
    try:
        p = Path(path)
        s = p.lstat()
        parent = p.parent.lstat()
        mode = stat.S_IMODE(s.st_mode)
        # Stat-only custody check; does not follow a symlink to key material.
        safe = (
            stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid()
            and s.st_nlink == 1 and s.st_size <= MAX_KEY_BYTES
            and not (mode & (0o077 if private else 0o022))
            and stat.S_ISDIR(parent.st_mode)
            and parent.st_uid == os.getuid()
            and not (stat.S_IMODE(parent.st_mode) & 0o022)
        )
        result = {
            "state": "metadata_acceptable" if safe else "metadata_unsafe",
            "metadata_acceptable": bool(safe),
            "public_pin_verified": False,
            "owner_is_observer": s.st_uid == os.getuid(),
            "parent_owner_is_observer": parent.st_uid == os.getuid(),
            "mode": format(mode, "04o"),
            "parent_mode": format(stat.S_IMODE(parent.st_mode), "04o"),
            "regular": stat.S_ISREG(s.st_mode), "symlink": stat.S_ISLNK(s.st_mode),
            "single_link": s.st_nlink == 1,
            "size_within_limit": s.st_size <= MAX_KEY_BYTES,
            "private_content_read": False,
        }
        if not private and safe and expected_public_sha256:
            try:
                if len(expected_public_sha256) != 64 or any(
                    x not in "0123456789abcdef" for x in expected_public_sha256.lower()
                ):
                    result["state"] = "invalid_public_pin"
                else:
                    # Public verifier material only, never the private issuer key.
                    public_digest = hashlib.sha256(p.read_bytes()).hexdigest()
                    result["public_pin_verified"] = (
                        public_digest == expected_public_sha256.lower()
                    )
                    if not result["public_pin_verified"]:
                        result["state"] = "public_pin_mismatch"
            except OSError:
                result["state"] = "public_pin_unavailable"
        return result
    except (OSError, ValueError):
        return {"state": "unavailable", "metadata_acceptable": False,
                "public_pin_verified": False}


def collect(role, path, expected_public_sha256):
    result = verify_metadata(path, private=(role == "issuer"),
                             expected_public_sha256=expected_public_sha256)
    return {
        "schema": "assistx.trace-key-custody-readonly.v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "key": result,
        "key_material_disclosed": False,
        "private_key_content_read": False,
        "escrow_rotation_independently_verified": False,
        "issuer_verifier_pair_proven": False,
        "production_dispatch_authorized": False,
        "promotion_eligible": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("issuer", "node"), required=True)
    parser.add_argument("--key-file", default="")
    parser.add_argument("--expected-public-sha256", default="")
    args = parser.parse_args()
    print(json.dumps(collect(args.role, args.key_file,
                             args.expected_public_sha256 or None), sort_keys=True))


if __name__ == "__main__":
    main()
