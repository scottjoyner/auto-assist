#!/usr/bin/env python3
"""Block sensitive .env path changes in public PRs without opening any file blob."""
from __future__ import annotations
import argparse
import json
import re
import subprocess

SHA = re.compile(r"^[0-9a-f]{40}$")
ENV = re.compile(r"^\.env($|[._-])", re.IGNORECASE)


def classify(data: bytes) -> dict:
    if not isinstance(data, bytes) or not data.endswith(b"\0"):
        raise ValueError("invalid metadata")
    tokens = [] if data == b"\0" else data[:-1].split(b"\0")
    if len(tokens) % 2:
        raise ValueError("incomplete metadata")
    blocked: dict[str, int] = {}
    for i in range(0, len(tokens), 2):
        status = tokens[i].decode("ascii")
        path = tokens[i + 1].decode("utf-8", "surrogateescape")
        if len(status) != 1 or status not in "ACDMRTUXB" or not path:
            raise ValueError("invalid name/status")
        if ENV.match(path.rsplit("/", 1)[-1]):
            blocked[status] = blocked.get(status, 0) + 1
    return {
        "schema_version": "assistx.public-env-path-guard-v1",
        "status": "HOLD_PUBLIC_ENV_DIFF" if blocked else "PASS_NO_ENV_DIFF",
        "changed_path_count": len(tokens) // 2,
        "sensitive_status_counts": dict(sorted(blocked.items())),
        "sensitive_change_count": sum(blocked.values()),
        "reads_file_contents": False,
        "credential_custody_closed": False,
        "production_release_authorized": False,
    }


def audit(base: str, head: str) -> dict:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError("invalid pinned commit")
    data = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-status", "-z", base, head, "--"],
        stderr=subprocess.DEVNULL,
        timeout=15,
    )
    return classify(data if data else b"\0")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    a = p.parse_args()
    try:
        result = audit(a.base, a.head)
    except (ValueError, OSError, subprocess.SubprocessError, UnicodeError):
        result = {"schema_version": "assistx.public-env-path-guard-v1",
                  "status": "HOLD_METADATA_UNAVAILABLE",
                  "reads_file_contents": False,
                  "production_release_authorized": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS_NO_ENV_DIFF" else 1


if __name__ == "__main__":
    raise SystemExit(main())
