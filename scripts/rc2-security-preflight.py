#!/usr/bin/env python3
"""Read-only AssistX RC2 security preflight; never opens environment contents.

This is a blocker/evidence report, NOT a substitute for secret rotation,
independent ingress inspection, physical graph fencing or production approval.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys


# Explicitly reviewed non-live paths; do not broaden based on a filename suffix.
ALLOWED_ENV_PATHS = frozenset(
    {".env.example", "archive/.env.committed-SECRETS-REMOVED"}
)
EXTERNAL_RELEASE_GATES = (
    "credential_ownership_and_rotation",
    "public_git_history_and_artifact_custody",
    "trusted_header_ingress_provenance",
    "trace_physical_inflight_cancellation",
    "authenticated_staging_negative_tests",
    "operator_release_authorization",
)


def _git(repo: pathlib.Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("read-only Git inventory failed")
    return result.stdout


def tracked_env_paths(paths: list[str]) -> list[str]:
    """Find tracked environment paths without opening or inspecting contents."""
    return sorted(
        path
        for path in paths
        if pathlib.PurePosixPath(path).name.startswith(".env")
        and path not in ALLOWED_ENV_PATHS
    )


def inspect(repo: pathlib.Path) -> dict[str, object]:
    """Return safe metadata; no secrets, config values, remote URLs or patches."""
    head = _git(repo, "rev-parse", "HEAD").decode("ascii").strip()
    path_bytes = _git(repo, "ls-files", "-z", "--cached")
    paths = [p.decode("utf-8", errors="replace") for p in path_bytes.split(b"\0") if p]
    offenders = tracked_env_paths(paths)
    return {
        "schema_version": "assistx.rc2.security-preflight.v1",
        "source_head": head,
        "tracked_environment_paths": offenders,
        "tracked_environment_count": len(offenders),
        "index_custody": "HOLD" if offenders else "PASS_INDEX_ONLY",
        "external_gates": {gate: "UNVERIFIED" for gate in EXTERNAL_RELEASE_GATES},
        "production_release_authorized": False,
        "reason": (
            "Tracked environment files remain in the index; custody/rotation "
            "and public history response require independent review."
            if offenders else
            "Index passes; historical exposure and independent production "
            "security attestations remain unverified."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--index-only", action="store_true",
        help="Exit successfully iff the current tracked-file index is clean; "
             "never establishes history/rotation/release approval.",
    )
    args = parser.parse_args(argv)
    try:
        result = inspect(args.repo)
    except RuntimeError:
        print(
            json.dumps({
                "schema_version": "assistx.rc2.security-preflight.v1",
                "index_custody": "UNKNOWN",
                "production_release_authorized": False,
                "reason": "Cannot inspect Git index; deny release.",
            }, sort_keys=True)
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    if args.index_only:
        return 2 if result["tracked_environment_count"] else 0
    # No unsigned/local JSON file may establish external release authority.
    return 2


if __name__ == "__main__":
    sys.exit(main())
