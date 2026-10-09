#!/usr/bin/env python3
"""Public-safe P0 credential-custody *evidence* preflight (never a release gate).

Reads only Git index *filenames* and an optional bounded, allowlisted JSON
checkpoint envelope. Does not read file bytes, container environments, keys,
tokens, Git history blobs, or provider accounts. No repository mutations.
Any supplied checkpoint is an untrusted operator *claim*: external custody,
revocation, audit and approval remain separately mandatory.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "assistx-custody-checkpoint/v1"
ARCHIVED = "archive/.env.committed-SECRETS-REMOVED"
TEMPLATES = frozenset((".env.example", ".env.kipnerter-gateway.example"))
STAGES = (
    "exposure_containment",
    "owner_classification",
    "issuer_revocation_rotation",
    "api_worker_hermes_rollout",
    "history_clone_artifact_response",
    "independent_private_verification",
    "owner_security_signoff",
)
REF = re.compile(r"^CUSTODY-[A-Z0-9][A-Z0-9-]{5,62}$")
ALLOWED_STATES = frozenset(("pending", "submitted"))
MAX_GIT_INDEX = 2_000_000  # bounded filename metadata, never file content
COMMIT_ID = re.compile(r"^[0-9a-f]{40}$")


def _tracked_env_count(repo: Path) -> tuple[int | None, str | None]:
    """Git reports pathnames only; do not surface individual paths."""
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--"],
            capture_output=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "index_unavailable"
    if p.returncode != 0 or len(p.stdout) > MAX_GIT_INDEX:
        return None, "index_unavailable"
    # Git can quote/path-encode only outside -z; use bytes for exact names.
    count = 0
    for item in p.stdout.split(b"\x00"):
        if not item:
            continue
        rel = item.decode("utf-8", "surrogateescape")
        basename = rel.rsplit("/", 1)[-1]
        if (basename.startswith(".env")
                and basename not in TEMPLATES
                and rel != ARCHIVED):
            count += 1
    return count, None


def _historical_env_count(repo: Path, commit_sha: str | None) -> tuple[int | None, str | None]:
    """Inspect filename metadata at one pinned commit; NOT an all-history scan.

    Ref names, revision expressions and arbitrary arguments are deliberately
    rejected. The caller may pin only an exact full Git commit SHA.
    """
    if commit_sha is None:
        return None, "historical_revision_not_checked"
    if not isinstance(commit_sha, str) or not COMMIT_ID.fullmatch(commit_sha):
        return None, "invalid_historical_commit_id"
    try:
        obj = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-t", commit_sha],
            capture_output=True, check=False, timeout=10,
        )
        if obj.returncode != 0 or obj.stdout.strip() != b"commit":
            return None, "historical_revision_not_a_commit"
        p = subprocess.run(
            ["git", "-C", str(repo), "ls-tree", "-r", "-z",
             "--name-only", commit_sha],
            capture_output=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "historical_index_unavailable"
    if p.returncode != 0 or len(p.stdout) > MAX_GIT_INDEX:
        return None, "historical_index_unavailable"
    count = 0
    for item in p.stdout.split(b"\x00"):
        if not item:
            continue
        rel = item.decode("utf-8", "surrogateescape")
        basename = rel.rsplit("/", 1)[-1]
        if (basename.startswith(".env")
                and basename not in TEMPLATES
                and rel != ARCHIVED):
            count += 1
    return count, None


def _check_envelope(evidence: Any) -> tuple[int, list[str]]:
    if evidence is None:
        return 0, ["owner_checkpoint_not_submitted"]
    if not isinstance(evidence, Mapping):
        return 0, ["invalid_checkpoint_format"]
    if set(evidence) != {"schema", "checkpoints"} or evidence.get("schema") != SCHEMA:
        return 0, ["invalid_checkpoint_schema"]
    checkpoints = evidence.get("checkpoints")
    if not isinstance(checkpoints, Mapping) or set(checkpoints) != set(STAGES):
        return 0, ["incomplete_checkpoint_matrix"]
    reasons: list[str] = []
    submitted = 0
    pending = False
    for stage in STAGES:
        record = checkpoints[stage]
        if not isinstance(record, Mapping) or set(record) != {"state", "reference"}:
            reasons.append("invalid_checkpoint_record")
            continue
        state = record.get("state")
        reference = record.get("reference")
        if not isinstance(state, str) or state not in ALLOWED_STATES:
            reasons.append("invalid_checkpoint_state")
            continue
        if state == "pending":
            pending = True
            if reference is not None:
                reasons.append("unexpected_pending_reference")
        else:
            # Opaque IDs only: URLs, filenames, env values and free text
            # cannot be published via this public result.
            if not isinstance(reference, str) or not REF.fullmatch(reference):
                reasons.append("invalid_opaque_reference")
            elif pending:
                reasons.append("out_of_order_checkpoint_claim")
            else:
                submitted += 1
    if submitted < len(STAGES):
        reasons.append("owner_checkpoints_incomplete_or_unverified")
    else:
        reasons.append("all_checkpoint_claims_still_unverified")
    return submitted, sorted(set(reasons))


def inspect(repo: Path, evidence: Any = None,
            historical_revision: str | None = None) -> dict[str, Any]:
    tracked_count, index_error = _tracked_env_count(repo)
    historical_count, historical_error = _historical_env_count(repo, historical_revision)
    submitted, reasons = _check_envelope(evidence)
    if index_error:
        reasons.append(index_error)
    elif tracked_count:
        reasons.append("tracked_environment_variants_present")
    else:
        reasons.append("clean_index_does_not_clear_historical_exposure")
    if historical_error:
        reasons.append(historical_error)
    elif historical_count:
        reasons.append("historical_revision_contains_env_variants")
    else:
        # Even a clean selected historical snapshot cannot bound other refs,
        # public clones, GitHub forks, CI artifacts, or exposed secret values.
        reasons.append("selected_history_snapshot_clean_not_comprehensive")
    reasons.extend((
        "live_secret_matches_not_checked",
        "independent_rotation_and_history_custody_unverified",
    ))
    # Deliberately never returns GO, even if the index is clean and every
    # untrusted self-attestation says "submitted".
    result = {
        "schema": "assistx-custody-readiness-observation/v1",
        "status": "HOLD",
        "production_authorized": False,
        "merge_authorized": False,
        "git_file_contents_read": False,
        "live_container_env_read": False,
        "history_blobs_read": False,
        "owner_checkpoint_claims_submitted": submitted,
        "owner_checkpoint_count_required": len(STAGES),
        "tracked_env_variant_count": tracked_count,
        "pinned_historical_revision_checked": historical_error is None,
        "historical_env_variant_count": historical_count,
        "reasons": sorted(set(reasons)),
    }
    result["observation_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--checkpoint-json", type=Path)
    parser.add_argument("--historical-commit", type=str)
    a = parser.parse_args()
    evidence: Any = None
    if a.checkpoint_json is not None:
        try:
            if a.checkpoint_json.stat().st_size > 16_384:
                raise ValueError("envelope_too_large")
            evidence = json.loads(a.checkpoint_json.read_text("utf-8"))
        except (OSError, ValueError, UnicodeError):
            evidence = {"invalid": True}  # do not print user-supplied data
    print(json.dumps(inspect(a.repo, evidence, a.historical_commit), sort_keys=True))
    return 1  # HOLD is a deliberate nonzero exit, never greenwashed


if __name__ == "__main__":
    raise SystemExit(main())
