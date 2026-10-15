#!/usr/bin/env python3
"""Offline, non-authorizing verification of a sanitized convergence report.

This checker validates that an evidence artifact matches a cutover manifest's
path, SHA-256, generation, checksum and revision digest. Passing is *not* a
production cutover approval or independent signature verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time
from typing import Any

import yaml

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_BYTES = 1024 * 1024
_TOP_KEYS = {
    "schema_version", "projection_view_schema_version", "verified_at_ts",
    "status", "failures", "assistx_projection", "router_projection",
    "retired_generations", "evidence_sha256",
}
_ID_KEYS = {"generation", "revision_sha256", "checksum"}
_ASSISTX_KEYS = _ID_KEYS | {"expires_at_ms", "provider_count"}
_ROUTER_KEYS = _ID_KEYS | {"admission", "admission_count"}


def _sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None


def _uint(value: Any, *, positive: bool = True) -> bool:
    return type(value) is int and (0 < value <= 2**63 - 1 if positive else 0 <= value <= 2**63 - 1)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate_key")
        obj[key] = value
    return obj


def _bad_constant(_: str) -> None:
    raise ValueError("non_json_constant")


def _identity(obj: Any, *, keys: set[str]) -> bool:
    return (
        isinstance(obj, dict)
        and set(obj) == keys
        and _uint(obj.get("generation"))
        and _sha(obj.get("checksum"))
        and _sha(obj.get("revision_sha256"))
    )


def _manifest_projection(manifest: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(manifest, dict):
        return None, ["manifest_not_mapping"]
    projection = manifest.get("runtime_projection")
    if not isinstance(projection, dict):
        return None, ["manifest_projection_missing"]
    errors = []
    if projection.get("convergence_status") != "pass":
        errors.append("manifest_convergence_not_pass")
    if not _uint(projection.get("generation")):
        errors.append("manifest_generation_invalid")
    if not _sha(projection.get("projection_checksum")):
        errors.append("manifest_checksum_invalid")
    revision = projection.get("revision")
    if not isinstance(revision, str) or not (1 <= len(revision) <= 300):
        errors.append("manifest_revision_invalid")
    else:
        try:
            revision.encode("utf-8")
        except UnicodeEncodeError:
            errors.append("manifest_revision_invalid")
    if not _sha(projection.get("convergence_evidence_sha256")):
        errors.append("manifest_artifact_digest_invalid")
    return projection, errors


def verify(manifest: Any, *, root: pathlib.Path, now_ms: int | None = None) -> list[str]:
    projection, errors = _manifest_projection(manifest)
    if errors or projection is None:
        return errors
    relative_path = projection.get("convergence_evidence_path")
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        return ["artifact_path_invalid"]
    relative = pathlib.PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "artifacts":
        return ["artifact_path_outside_artifacts"]
    root = root.resolve()
    path = root.joinpath(*relative.parts).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return ["artifact_path_escapes_root"]
    if not path.is_file():
        return ["artifact_missing"]
    if path.stat().st_size > _MAX_BYTES:
        return ["artifact_excessive_size"]
    try:
        raw = path.read_bytes()
    except OSError:
        return ["artifact_unreadable"]
    if hashlib.sha256(raw).hexdigest() != projection["convergence_evidence_sha256"]:
        return ["artifact_file_digest_mismatch"]
    try:
        artifact = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_bad_constant)
    except (UnicodeError, ValueError, RecursionError):
        return ["artifact_json_invalid"]
    if not isinstance(artifact, dict) or set(artifact) != _TOP_KEYS:
        return ["artifact_schema_fields_invalid"]
    if artifact.get("schema_version") != 1 or artifact.get("projection_view_schema_version") != 2:
        return ["artifact_view_version_unrecognized"]
    if artifact.get("status") != "pass" or artifact.get("failures") != []:
        return ["artifact_convergence_not_pass"]
    if not _uint(artifact.get("verified_at_ts")):
        return ["artifact_timestamp_invalid"]
    embedded_sha = artifact.get("evidence_sha256")
    if not _sha(embedded_sha):
        return ["artifact_embedded_digest_invalid"]
    unsigned = {k: v for k, v in artifact.items() if k != "evidence_sha256"}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != embedded_sha:
        return ["artifact_embedded_digest_mismatch"]

    source = artifact.get("assistx_projection")
    target = artifact.get("router_projection")
    if not _identity(source, keys=_ASSISTX_KEYS) or not _identity(target, keys=_ROUTER_KEYS):
        return ["artifact_projection_identity_invalid"]
    if not _uint(source.get("expires_at_ms")):
        return ["artifact_expiry_invalid"]
    if not _uint(source.get("provider_count")) or source["provider_count"] > 1024:
        return ["artifact_provider_count_invalid"]
    admission = target.get("admission")
    if (
        not isinstance(admission, list) or not (1 <= len(admission) <= 1024)
        or target.get("admission_count") != len(admission)
        or any(
            not isinstance(item, dict) or set(item) != {"parallel_slots"}
            or not _uint(item.get("parallel_slots"))
            for item in admission
        )
    ):
        return ["artifact_admission_invalid"]
    retired = artifact.get("retired_generations")
    if (
        not isinstance(retired, list) or len(retired) > 16
        or any(not _identity(item, keys=_ID_KEYS) for item in retired)
    ):
        return ["artifact_retired_generations_invalid"]
    if source["generation"] != target["generation"] or source["checksum"] != target["checksum"]:
        return ["artifact_cross_projection_mismatch"]
    if source["revision_sha256"] != target["revision_sha256"]:
        return ["artifact_revision_mismatch"]
    if source["generation"] != projection["generation"]:
        return ["artifact_manifest_generation_mismatch"]
    if source["checksum"] != projection["projection_checksum"]:
        return ["artifact_manifest_checksum_mismatch"]
    revision_digest = hashlib.sha256(projection["revision"].encode("utf-8")).hexdigest()
    if source["revision_sha256"] != revision_digest:
        return ["artifact_manifest_revision_mismatch"]
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if not _uint(current_ms, positive=False) or source["expires_at_ms"] <= current_ms:
        return ["artifact_projection_expired"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only v2 convergence report file, hash and schema consistency check."
    )
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    args = parser.parse_args()
    try:
        document = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
        errors = verify(document, root=args.root)
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        errors = ["manifest_unreadable_or_invalid"]
    if errors:
        print("CONVERGENCE_READBACK: BLOCKED " + ",".join(sorted(set(errors))), file=sys.stderr)
        return 1
    print("CONVERGENCE_READBACK: VERIFIED_ARTIFACT_CONSISTENCY_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
