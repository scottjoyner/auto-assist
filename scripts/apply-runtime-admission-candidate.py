#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import stat
import sys
import time
from typing import Any

from neo4j import GraphDatabase

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from assistx.runtime_admission import (  # noqa: E402
    RuntimeAdmissionContractError,
    validate_runtime_admission_candidate,
)


def _load_projection_approver() -> Any:
    path = ROOT / "scripts" / "approve-runtime-projection.py"
    spec = importlib.util.spec_from_file_location(
        "assistx_approve_runtime_projection",
        path,
    )
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load projection approver from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _safe_candidate(path_text: str) -> pathlib.Path:
    path = pathlib.Path(path_text).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError("candidate must be a regular nonsymlinked file")
    if stat.S_IMODE(path.stat().st_mode) & 0o022:
        raise ValueError("candidate must not be group/world writable")
    return path


def _load_candidate(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("candidate must contain a JSON object")
    return value


def _persist_contract(tx: Any, candidate: dict[str, Any], now_ms: int) -> None:
    lease = candidate["lease"]
    profile = candidate["profile"]
    evidence = candidate["evidence"]
    runtime = candidate["runtime"]
    manifest = candidate["projection_manifest"]
    generation = int(lease["generation"])
    expires_at_ms = int(lease["expires_at_ms"])

    tx.run(
        """
        MATCH (old:RuntimeAdmissionLease)
        WHERE old.state='ACTIVE' AND old.generation < $generation
        SET old.state='SUPERSEDED',
            old.revoked_at_ms=$now_ms,
            old.revocation_reason='newer_generation_applied',
            old.updated_at=datetime(),
            old.updated_at_ts=timestamp()
        """,
        generation=generation,
        now_ms=now_ms,
    ).consume()

    tx.run(
        """
        MERGE (candidate:RuntimeAdmissionCandidate {
            candidate_id:$candidate_id
        })
        ON CREATE SET candidate.created_at=datetime(),
                      candidate.created_at_ts=timestamp()
        SET candidate.schema_version=$schema_version,
            candidate.candidate_fingerprint=$candidate_fingerprint,
            candidate.profile_id=$profile_id,
            candidate.profile_revision=$profile_revision,
            candidate.profile_fingerprint=$profile_fingerprint,
            candidate.loadout_fingerprint=$loadout_fingerprint,
            candidate.live_proof_fingerprint=$live_proof_fingerprint,
            candidate.bundle_fingerprint=$bundle_fingerprint,
            candidate.qualification_fingerprint=$qualification_fingerprint,
            candidate.qualification_attestation_fingerprint=
                $qualification_attestation_fingerprint,
            candidate.canary_manifest_fingerprint=
                $canary_manifest_fingerprint,
            candidate.canary_attestation_fingerprint=
                $canary_attestation_fingerprint,
            candidate.canary_signer_identity=$canary_signer_identity,
            candidate.canary_signing_key_fingerprint=
                $canary_signing_key_fingerprint,
            candidate.runtime_instance_id=$runtime_instance_id,
            candidate.node_id=$node_id,
            candidate.model_instance_id=$model_instance_id,
            candidate.artifact_fingerprint=$artifact_fingerprint,
            candidate.rollback_profile_id=$rollback_profile_id,
            candidate.profile_admission_enabled=false,
            candidate.status='APPROVED',
            candidate.generation=$generation,
            candidate.approved_by=$approved_by,
            candidate.approval_id=$approval_id,
            candidate.approved_at_ms=$now_ms,
            candidate.expires_at_ms=$expires_at_ms,
            candidate.candidate_json=$candidate_json,
            candidate.updated_at=datetime(),
            candidate.updated_at_ts=timestamp()
        """,
        candidate_id=candidate["candidate_id"],
        schema_version=candidate["schema_version"],
        candidate_fingerprint=candidate["candidate_fingerprint"],
        profile_id=profile["profile_id"],
        profile_revision=int(profile["revision"]),
        profile_fingerprint=profile["fingerprint"],
        loadout_fingerprint=candidate["loadout_fingerprint"],
        live_proof_fingerprint=evidence["live_proof_fingerprint"],
        bundle_fingerprint=evidence.get("bundle_fingerprint"),
        qualification_fingerprint=evidence.get("qualification_fingerprint"),
        qualification_attestation_fingerprint=evidence.get(
            "qualification_attestation_fingerprint"
        ),
        canary_manifest_fingerprint=evidence.get(
            "canary_manifest_fingerprint"
        ),
        canary_attestation_fingerprint=evidence.get(
            "canary_attestation_fingerprint"
        ),
        canary_signer_identity=evidence.get("canary_signer_identity"),
        canary_signing_key_fingerprint=evidence.get(
            "canary_signing_key_fingerprint"
        ),
        runtime_instance_id=runtime["runtime_instance_id"],
        node_id=runtime["node_id"],
        model_instance_id=runtime["model_instance_id"],
        artifact_fingerprint=runtime["artifact_fingerprint"],
        rollback_profile_id=runtime["rollback"]["profile_id"],
        generation=generation,
        approved_by=lease["approved_by"],
        approval_id=lease["approval_id"],
        now_ms=now_ms,
        expires_at_ms=expires_at_ms,
        candidate_json=json.dumps(
            candidate,
            sort_keys=True,
            separators=(",", ":"),
        ),
    ).consume()

    tx.run(
        """
        MERGE (lease:RuntimeAdmissionLease {lease_id:$lease_id})
        ON CREATE SET lease.created_at=datetime(),
                      lease.created_at_ts=timestamp()
        SET lease.schema_version=$schema_version,
            lease.state='ACTIVE',
            lease.candidate_id=$candidate_id,
            lease.candidate_fingerprint=$candidate_fingerprint,
            lease.profile_id=$profile_id,
            lease.profile_revision=$profile_revision,
            lease.runtime_instance_id=$runtime_instance_id,
            lease.model_instance_id=$model_instance_id,
            lease.artifact_fingerprint=$artifact_fingerprint,
            lease.loadout_fingerprint=$loadout_fingerprint,
            lease.generation=$generation,
            lease.approved_by=$approved_by,
            lease.approval_id=$approval_id,
            lease.issued_at_ms=$issued_at_ms,
            lease.expires_at_ms=$expires_at_ms,
            lease.revocable=true,
            lease.updated_at=datetime(),
            lease.updated_at_ts=timestamp()
        WITH lease
        MATCH (candidate:RuntimeAdmissionCandidate {
            candidate_id:$candidate_id
        })
        MERGE (candidate)-[:AUTHORIZED_BY]->(lease)
        """,
        lease_id=lease["lease_id"],
        schema_version=lease["schema_version"],
        candidate_id=candidate["candidate_id"],
        candidate_fingerprint=candidate["candidate_fingerprint"],
        profile_id=profile["profile_id"],
        profile_revision=int(profile["revision"]),
        runtime_instance_id=runtime["runtime_instance_id"],
        model_instance_id=runtime["model_instance_id"],
        artifact_fingerprint=runtime["artifact_fingerprint"],
        loadout_fingerprint=candidate["loadout_fingerprint"],
        generation=generation,
        approved_by=lease["approved_by"],
        approval_id=lease["approval_id"],
        issued_at_ms=int(lease["issued_at_ms"]),
        expires_at_ms=expires_at_ms,
    ).consume()

    tx.run(
        """
        MATCH (state:FleetProjectionState {name:'canonical'})
        WHERE state.generation=$generation
        SET state.admission_candidate_id=$candidate_id,
            state.admission_candidate_fingerprint=$candidate_fingerprint,
            state.admission_lease_id=$lease_id,
            state.admission_lease_expires_at_ms=$expires_at_ms,
            state.profile_id=$profile_id,
            state.profile_revision=$profile_revision,
            state.profile_fingerprint=$profile_fingerprint,
            state.loadout_fingerprint=$loadout_fingerprint,
            state.live_proof_fingerprint=$live_proof_fingerprint,
            state.updated_at=datetime(),
            state.updated_at_ts=timestamp()
        WITH state
        MATCH (lease:RuntimeAdmissionLease {lease_id:$lease_id})
        MERGE (state)-[:AUTHORIZED_BY]->(lease)
        """,
        generation=generation,
        candidate_id=candidate["candidate_id"],
        candidate_fingerprint=candidate["candidate_fingerprint"],
        lease_id=lease["lease_id"],
        expires_at_ms=expires_at_ms,
        profile_id=profile["profile_id"],
        profile_revision=int(profile["revision"]),
        profile_fingerprint=profile["fingerprint"],
        loadout_fingerprint=candidate["loadout_fingerprint"],
        live_proof_fingerprint=evidence["live_proof_fingerprint"],
    ).consume()

    tx.run(
        """
        MATCH (runtime:RuntimeInstance {
            runtime_instance_id:$runtime_instance_id
        })
        WHERE runtime.projection_generation=$generation
        SET runtime.admission_candidate_id=$candidate_id,
            runtime.admission_candidate_fingerprint=$candidate_fingerprint,
            runtime.admission_lease_id=$lease_id,
            runtime.admission_lease_expires_at_ms=$expires_at_ms,
            runtime.profile_id=$profile_id,
            runtime.profile_revision=$profile_revision,
            runtime.profile_fingerprint=$profile_fingerprint,
            runtime.loadout_fingerprint=$loadout_fingerprint,
            runtime.live_proof_fingerprint=$live_proof_fingerprint,
            runtime.rollback_profile_id=$rollback_profile_id,
            runtime.rollback_health_verified=true,
            runtime.boot_recovery_verified=true,
            runtime.shared_capacity_key=$shared_capacity_key,
            runtime.updated_at=datetime(),
            runtime.updated_at_ts=timestamp()
        WITH runtime
        MATCH (lease:RuntimeAdmissionLease {lease_id:$lease_id})
        MERGE (lease)-[:ADMITS]->(runtime)
        """,
        runtime_instance_id=runtime["runtime_instance_id"],
        generation=generation,
        candidate_id=candidate["candidate_id"],
        candidate_fingerprint=candidate["candidate_fingerprint"],
        lease_id=lease["lease_id"],
        expires_at_ms=expires_at_ms,
        profile_id=profile["profile_id"],
        profile_revision=int(profile["revision"]),
        profile_fingerprint=profile["fingerprint"],
        loadout_fingerprint=candidate["loadout_fingerprint"],
        live_proof_fingerprint=evidence["live_proof_fingerprint"],
        rollback_profile_id=runtime["rollback"]["profile_id"],
        shared_capacity_key=runtime["capacity"]["shared_capacity_key"],
    ).consume()

    if manifest.get("generation") != generation:
        raise RuntimeError("candidate and projection generation diverged")


def _apply_transaction(
    tx: Any,
    approver: Any,
    candidate: dict[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    validated_manifest = approver.validate_manifest(
        candidate["projection_manifest"]
    )
    result = approver._apply_transaction(tx, validated_manifest, now_ms)
    _persist_contract(tx, candidate, now_ms)
    return {
        **dict(result or {}),
        "candidate_id": candidate["candidate_id"],
        "candidate_fingerprint": candidate["candidate_fingerprint"],
        "lease_id": candidate["lease"]["lease_id"],
        "lease_expires_at_ms": candidate["lease"]["expires_at_ms"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Atomically apply a verified runtime admission candidate, "
            "its expiring lease, and the fenced AssistX runtime projection."
        )
    )
    parser.add_argument("--candidate", required=True)
    parser.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI", ""),
    )
    parser.add_argument(
        "--neo4j-user",
        default=os.getenv("NEO4J_USER", ""),
    )
    parser.add_argument(
        "--neo4j-password",
        default=os.getenv("NEO4J_PASSWORD", ""),
    )
    parser.add_argument(
        "--neo4j-database",
        default=os.getenv("NEO4J_DATABASE", "neo4j"),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        path = _safe_candidate(args.candidate)
        candidate = validate_runtime_admission_candidate(
            _load_candidate(path)
        )
        approver = _load_projection_approver()
        approver.validate_manifest(candidate["projection_manifest"])
    except (OSError, ValueError, RuntimeAdmissionContractError) as exc:
        print(f"runtime admission candidate rejected: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(
            json.dumps(
                {
                    "candidate_id": candidate["candidate_id"],
                    "candidate_fingerprint": candidate[
                        "candidate_fingerprint"
                    ],
                    "lease_id": candidate["lease"]["lease_id"],
                    "lease_expires_at_ms": candidate["lease"][
                        "expires_at_ms"
                    ],
                    "generation": candidate["lease"]["generation"],
                    "dry_run": True,
                    "applied": False,
                },
                sort_keys=True,
            )
        )
        return 0

    if not all(
        str(value or "").strip()
        for value in (
            args.neo4j_uri,
            args.neo4j_user,
            args.neo4j_password,
            args.neo4j_database,
        )
    ):
        print(
            "Neo4j URI, user, password, and database are required",
            file=sys.stderr,
        )
        return 2

    driver = GraphDatabase.driver(
        args.neo4j_uri,
        auth=(args.neo4j_user, args.neo4j_password),
    )
    now_ms = int(time.time() * 1000)
    try:
        with driver.session(database=args.neo4j_database) as session:
            result = session.execute_write(
                _apply_transaction,
                approver,
                candidate,
                now_ms,
            )
    except Exception as exc:
        print(f"runtime admission apply failed: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()

    print(
        json.dumps(
            {
                **result,
                "dry_run": False,
                "applied": True,
            },
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
