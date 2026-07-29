from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

ALLOWED_ACTIONS = {
    "collect_evidence", "refresh_agent", "reload_model", "drain_and_test",
    "drain_and_benchmark", "restore_service", "restart_service",
    "redeploy_service", "drain_node", "resume_node", "health_check",
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
            "model_id": diagnosis.get("model_id"),
            "action": action,
            "parameters": recovery.get("parameters") or {},
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

    def reconcile(
        self,
        store: Any,
        *,
        now: int | None = None,
        approved_timeout_seconds: int = 1800,
        executing_timeout_seconds: int = 900,
        dispatched_timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        return store.reconcile(
            now=int(now if now is not None else time.time()),
            approved_timeout_seconds=approved_timeout_seconds,
            executing_timeout_seconds=executing_timeout_seconds,
            dispatched_timeout_seconds=dispatched_timeout_seconds,
        )


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
        audit_id = f"recovery-audit-{uuid.uuid4().hex}"
        with self.neo._session() as session:
            row = session.run(
                """
                MATCH (p:FleetRecovery {id:$id, status:$expected})
                SET p.status=$status, p.last_actor=$actor, p.updated_at_ts=timestamp(),
                    p.result_json=$result
                CREATE (a:RecoveryAuditEvent {
                    id:$audit_id, proposal_id:$id, from_status:$expected,
                    to_status:$status, actor:$actor, result_json:$result,
                    created_at_ts:timestamp()
                })
                MERGE (p)-[:HAS_AUDIT_EVENT]->(a)
                RETURN p
                """,
                {"id": proposal_id, "expected": expected, "status": status,
                 "actor": actor, "result": json.dumps(result or {}, sort_keys=True),
                 "audit_id": audit_id},
            ).single()
        return _decode(dict(row["p"])) if row else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.neo._session() as session:
            rows = session.run(
                "MATCH (p:FleetRecovery) RETURN p ORDER BY p.updated_at_ts DESC LIMIT $limit",
                {"limit": limit},
            )
            return [_decode(dict(row["p"])) for row in rows]

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.neo._session() as session:
            rows = session.run(
                """
                MATCH (a:RecoveryAuditEvent)
                RETURN a ORDER BY a.created_at_ts DESC LIMIT $limit
                """,
                {"limit": limit},
            )
            return [dict(row["a"]) for row in rows]

    def reconcile(
        self,
        *,
        now: int,
        approved_timeout_seconds: int,
        executing_timeout_seconds: int,
        dispatched_timeout_seconds: int,
    ) -> dict[str, Any]:
        rules = [
            ("PROPOSED", "EXPIRED", "expires_at_ts", now, "proposal_expired"),
            ("APPROVED", "EXPIRED_APPROVAL", "updated_at_ts", now - approved_timeout_seconds, "approval_dispatch_timeout"),
            ("EXECUTING", "FAILED_STUCK", "updated_at_ts", now - executing_timeout_seconds, "dispatch_stuck"),
            ("DISPATCHED", "FAILED_TIMEOUT", "updated_at_ts", now - dispatched_timeout_seconds, "runbook_timeout"),
        ]
        counts: dict[str, int] = {}
        with self.neo._session() as session:
            for source, target, field, cutoff, reason in rules:
                query = f"""
                    MATCH (p:FleetRecovery {{status:$source}})
                    WHERE p.{field} < $cutoff
                    SET p.status=$target, p.last_actor='recovery-reconciler',
                        p.updated_at_ts=$now, p.reconcile_reason=$reason
                    WITH p
                    CREATE (a:RecoveryAuditEvent {{
                        id:randomUUID(), proposal_id:p.id, from_status:$source,
                        to_status:$target, actor:'recovery-reconciler',
                        result_json:$result, created_at_ts:$now
                    }})
                    MERGE (p)-[:HAS_AUDIT_EVENT]->(a)
                    RETURN count(p) AS count
                """
                row = session.run(
                    query,
                    {
                        "source": source,
                        "target": target,
                        "cutoff": cutoff,
                        "now": now,
                        "reason": reason,
                        "result": json.dumps({"reason": reason}),
                    },
                ).single()
                counts[target] = int(row["count"] if row else 0)
        return {"reconciled": sum(counts.values()), "transitions": counts, "at": now}


_reconciler_thread: threading.Thread | None = None
_reconciler_stop = threading.Event()


def start_recovery_reconciler(neo_factory: Callable[[], Any]) -> None:
    global _reconciler_thread
    if _reconciler_thread and _reconciler_thread.is_alive():
        return
    interval = max(30, int(os.getenv("ASSISTX_RECOVERY_RECONCILE_INTERVAL_SECONDS", "60")))

    def loop() -> None:
        while not _reconciler_stop.wait(interval):
            neo = neo_factory()
            try:
                RecoveryControlPlane().reconcile(Neo4jRecoveryStore(neo))
            except Exception:
                pass
            finally:
                neo.close()

    _reconciler_stop.clear()
    _reconciler_thread = threading.Thread(
        target=loop,
        name="recovery-reconciler",
        daemon=True,
    )
    _reconciler_thread.start()


def _decode(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["plan"] = json.loads(result.pop("plan_json", "{}") or "{}")
    result["result"] = json.loads(result.pop("result_json", "{}") or "{}")
    return result
