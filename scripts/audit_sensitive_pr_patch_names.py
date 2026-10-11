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


MAX_ANCESTRY_COMMITS = 256


def _git_metadata(*args: str) -> bytes:
    # Only request commit IDs and file *names/statuses*, never blob data,
    # patches, commit messages, or the contents of historical env files.
    return subprocess.check_output(
        ["git", *args], stderr=subprocess.DEVNULL, timeout=15,
    )


def audit(base: str, head: str) -> dict:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError("invalid pinned commit")

    # The final base-to-head diff is not enough: a previously pushed commit
    # can publish a secret-bearing file and then delete it before PR review.
    # Inspect every reachable commit since the fork, including merged side\n    # branches; an add-delete on a second parent must not escape review.
    fork = _git_metadata("merge-base", base, head).decode("ascii").strip()
    if not SHA.fullmatch(fork):
        raise ValueError("invalid merge base")
    revisions = _git_metadata(
        "rev-list", "--reverse", "--parents",
        f"{fork}..{head}",
    ).decode("ascii").splitlines()
    if len(revisions) > MAX_ANCESTRY_COMMITS:
        raise ValueError("too many commits for bounded custody review")

    final_diff = _git_metadata(
        "diff", "--no-renames", "--name-status", "-z", base, head, "--",
    )
    report = classify(final_diff if final_diff else b"\0")
    net_sensitive = report["sensitive_change_count"]
    history_sensitive = 0
    touched_commits = 0
    for revision in revisions:
        parts = revision.split()
        if len(parts) < 2 or not all(SHA.fullmatch(x) for x in parts):
            raise ValueError("invalid commit ancestry")
        commit, first_parent = parts[:2]
        delta = _git_metadata(
            "diff", "--no-renames", "--name-status", "-z",
            first_parent, commit, "--",
        )
        record = classify(delta if delta else b"\0")
        if record["sensitive_change_count"]:
            touched_commits += 1
            history_sensitive += record["sensitive_change_count"]

    report.update(
        ancestry_commits_examined=len(revisions),
        historical_sensitive_commit_count=touched_commits,
        historical_sensitive_change_count=history_sensitive,
        net_sensitive_change_count=net_sensitive,
        # An add-then-delete pair is still publicly reachable by commit SHA.
        status="HOLD_PUBLIC_ENV_HISTORY" if history_sensitive
        else "HOLD_PUBLIC_ENV_DIFF" if net_sensitive
        else "PASS_NO_ENV_DIFF",
        # A clean patch is never an attestation that old tracked credentials
        # have been revoked, rotated or removed from Git history.
        credential_custody_closed=False,
        production_release_authorized=False,
    )
    return report

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
