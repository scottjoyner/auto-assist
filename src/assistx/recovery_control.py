from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from typing import Any, Callable


ALLOWED_ACTIONS = {
    "collect_evidence", "refresh_agent", "reload_model", "drain_and_test",
    "drain_and_benchmark", "restore_service",
}


def recovery_fingerprint(plan: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class RecoveryControlPlane:
    def __init__(self) -> None:
        self.execution_enabled = os.getenv(
            "ASSISTX_RECOVERY_EXECUTION_ENABLED", "false"
        ).lower() in {"1", "true", "yes", "on"}

    def propose(self, store: Any, diagnosis: dict[str, Any], actor: str) -> dict[str, Any]:
        recovery = diagnosis.get("recommended_recovery") or {}
        action = recovery.get("action")
        if action not in ALLOWED_ACTIONS:
            raise ValueError("unsupported recovery action")
        plan = {
            "diagnosis_id": diagnosis.get("diagnosis_id"),
            "incident_key": diagnosis.get("incident_key"),
            "node_id": diagnosis.get("node_id"),
            "action": action,
            "risk": recovery.get("risk"),
            "verify_after": recovery.get("verify_after") or [],
            "rollback": recovery.get("rollback"),
        }
        return store.create(plan, actor, recovery_fingerprint(plan))

    def approve(self, store: Any, proposal_id: str, fingerprint: str, actor: str) -> dict[str, Any]:
        proposal = store.get(proposal_id)
        if not proposal or proposal.get("status") != "PROPOSED":
            raise ValueError("proposal is not awaiting approval")
        if proposal.get("fingerprint") != fingerprint:
            raise ValueError("proposal fingerprint mismatch")
        if int(proposal.get("expires_at_ts") or 0) < int(time.time()):
            raise ValueError("proposal expired")
        result = store.transition(proposal_id, "PROPOSED", "APPROVED", actor)
        if not result:
            raise ValueError("proposal changed concurrently")
        return result

    def execute(
        self,
        store: Any,
        proposal_id: str,
        actor: str,
        dispatch: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.execution_enabled:
            return {"executed": False, "blocked": True, "reason": "recovery_execution_disabled"}
        proposal = store.get(proposal_id)
        if not proposal or proposal.get("status") != "APPROVED":
            return {"executed": False, "blocked": True, "reason": "proposal_not_approved"}
        executing = store.transition(proposal_id, "APPROVED", "EXECUTING", actor)
        if not executing:
            return {"executed": False, "blocked": True, "reason": "proposal_changed_concurrently"}
        result = dispatch(proposal["plan"])
        status = "DISPATCHED" if result.get("task_id") else "FAILED"
        final = store.transition(proposal_id, "EXECUTING", status, actor, result)
        return {"executed": bool(result.get("task_id")), "proposal": final, **result}

    def record_outcome(
        self,
        store: Any,
        proposal_id: str,
        actor: str,
        *,
        verified: bool,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        proposal = store.get(proposal_id)
        if not proposal or proposal.get("status") != "DISPATCHED":
            raise ValueError("only a dispatched recovery can record an outcome")
        status = "VERIFIED" if verified else "FAILED_VERIFICATION"
        result = store.transition(
            proposal_id,
            "DISPATCHED",
            status,
            actor,
            {"verified": verified, "evidence": evidence, "feeds_improvement_memory": True},
        )
        if not result:
            raise ValueError("proposal changed concurrently")
        return result


class Neo4jRecoveryStore:
    def __init__(self, neo: Any):
        self.neo = neo

    def create(self, plan: dict[str, Any], actor: str, fingerprint: str, ttl_seconds: int = 1800) -> dict[str, Any]:
        now, proposal_id = int(time.time()), f"recovery-{uuid.uuid4().hex}"
        with self.neo._session() as session:
            row = session.run(
                """
                CREATE (p:FleetRecovery {
                    id:$id, fingerprint:$fingerprint, plan_json:$plan,
                    status:'PROPOSED', proposed_by:$actor,
                    created_at_ts:$now, updated_at_ts:$now, expires_at_ts:$expires
                }) RETURN p
                """,
                {"id": proposal_id, "fingerprint": fingerprint, "plan": json.dumps(plan, sort_keys=True),
                 "actor": actor, "now": now, "expires": now + ttl_seconds},
            ).single()
        return _decode(dict(row["p"])) if row else {}

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        with self.neo._session() as session:
            row = session.run("MATCH (p:FleetRecovery {id:$id}) RETURN p", {"id": proposal_id}).single()
        return _decode(dict(row["p"])) if row else None

    def transition(self, proposal_id: str, expected: str, status: str, actor: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self.neo._session() as session:
            row = session.run(
                """
                MATCH (p:FleetRecovery {id:$id, status:$expected})
                SET p.status=$status, p.last_actor=$actor, p.updated_at_ts=timestamp(),
                    p.result_json=$result
                RETURN p
                """,
                {"id": proposal_id, "expected": expected, "status": status,
                 "actor": actor, "result": json.dumps(result or {}, sort_keys=True)},
            ).single()
        return _decode(dict(row["p"])) if row else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.neo._session() as session:
            rows = session.run(
                "MATCH (p:FleetRecovery) RETURN p ORDER BY p.updated_at_ts DESC LIMIT $limit",
                {"limit": limit},
            )
            return [_decode(dict(row["p"])) for row in rows]


def _decode(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["plan"] = json.loads(result.pop("plan_json", "{}") or "{}")
    result["result"] = json.loads(result.pop("result_json", "{}") or "{}")
    return result
