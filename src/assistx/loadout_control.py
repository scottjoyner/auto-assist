from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from typing import Any, Callable


def proposal_fingerprint(action: dict[str, Any]) -> str:
    canonical = json.dumps(action, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class Neo4jLoadoutStore:
    def __init__(self, neo: Any):
        self.neo = neo

    def create(self, action: dict[str, Any], actor: str, ttl_seconds: int = 1800) -> dict[str, Any]:
        proposal_id = f"loadout-{uuid.uuid4().hex}"
        fingerprint = proposal_fingerprint(action)
        now = int(time.time())
        with self.neo._session() as session:
            row = session.run(
                """
                CREATE (p:FleetLoadoutChange {
                    id:$id, fingerprint:$fingerprint, action_json:$action_json,
                    status:'PROPOSED', proposed_by:$actor,
                    created_at_ts:$now, updated_at_ts:$now, expires_at_ts:$expires
                })
                RETURN p
                """,
                {
                    "id": proposal_id,
                    "fingerprint": fingerprint,
                    "action_json": json.dumps(action, sort_keys=True),
                    "actor": actor,
                    "now": now,
                    "expires": now + ttl_seconds,
                },
            ).single()
        return _decode(dict(row["p"])) if row else {}

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        with self.neo._session() as session:
            row = session.run(
                "MATCH (p:FleetLoadoutChange {id:$id}) RETURN p",
                {"id": proposal_id},
            ).single()
        return _decode(dict(row["p"])) if row else None

    def transition(
        self,
        proposal_id: str,
        expected_status: str,
        status: str,
        actor: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = int(time.time())
        with self.neo._session() as session:
            row = session.run(
                """
                MATCH (p:FleetLoadoutChange {id:$id, status:$expected})
                SET p.status=$status, p.updated_at_ts=$now,
                    p.last_actor=$actor, p.result_json=$result_json
                RETURN p
                """,
                {
                    "id": proposal_id,
                    "expected": expected_status,
                    "status": status,
                    "now": now,
                    "actor": actor,
                    "result_json": json.dumps(metadata or {}, sort_keys=True),
                },
            ).single()
        return _decode(dict(row["p"])) if row else None


class LoadoutControlPlane:
    def __init__(self) -> None:
        self.execution_enabled = os.getenv("ASSISTX_LOADOUT_EXECUTION_ENABLED", "false").lower() in {"1", "true", "yes"}

    def propose(self, store: Any, action: dict[str, Any], actor: str) -> dict[str, Any]:
        _validate_action(action)
        return store.create(action, actor)

    def approve(self, store: Any, proposal_id: str, fingerprint: str, actor: str) -> dict[str, Any]:
        proposal = store.get(proposal_id)
        if not proposal:
            raise ValueError("proposal not found")
        if proposal.get("status") != "PROPOSED":
            raise ValueError("proposal is not awaiting approval")
        if int(proposal.get("expires_at_ts") or 0) < int(time.time()):
            raise ValueError("proposal expired")
        if fingerprint != proposal.get("fingerprint"):
            raise ValueError("proposal fingerprint mismatch")
        result = store.transition(proposal_id, "PROPOSED", "APPROVED", actor)
        if not result:
            raise ValueError("proposal changed concurrently")
        return result

    def execute(
        self,
        store: Any,
        proposal_id: str,
        actor: str,
        network_map: dict[str, Any],
        load: Callable[[str, str], dict[str, Any]],
        unload: Callable[[str, str], dict[str, Any]],
        verify: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.execution_enabled:
            return {"executed": False, "blocked": True, "reason": "execution_disabled"}
        proposal = store.get(proposal_id)
        if not proposal or proposal.get("status") != "APPROVED":
            return {"executed": False, "blocked": True, "reason": "proposal_not_approved"}
        action = proposal.get("action") or {}
        node = next((row for row in network_map.get("nodes") or [] if row.get("id") == action.get("node_id")), None)
        valid, reason, base_url = _revalidate(action, node)
        if not valid:
            store.transition(proposal_id, "APPROVED", "BLOCKED", actor, {"reason": reason})
            return {"executed": False, "blocked": True, "reason": reason}
        executing = store.transition(proposal_id, "APPROVED", "EXECUTING", actor)
        if not executing:
            return {"executed": False, "blocked": True, "reason": "proposal_changed_concurrently"}
        model = str(action["model_id"])
        operation = load(base_url, model) if action["action"] == "replicate_candidate" else unload(base_url, model)
        observed = verify(base_url)
        loaded = set(observed.get("loaded_models") or observed.get("models") or [])
        expected_loaded = action["action"] == "replicate_candidate"
        verified = bool(operation.get("ok")) and ((model in loaded) == expected_loaded)
        if verified:
            final = store.transition(proposal_id, "EXECUTING", "COMPLETED", actor, {"operation": operation, "verification": observed})
            return {"executed": True, "verified": True, "proposal": final}
        rollback = unload(base_url, model) if expected_loaded else load(base_url, model)
        final = store.transition(
            proposal_id,
            "EXECUTING",
            "ROLLED_BACK",
            actor,
            {"operation": operation, "verification": observed, "rollback": rollback},
        )
        return {"executed": True, "verified": False, "rolled_back": True, "proposal": final}


def _validate_action(action: dict[str, Any]) -> None:
    if action.get("action") not in {"replicate_candidate", "unload_candidate"}:
        raise ValueError("action must be a simulated replicate_candidate or unload_candidate")
    if not action.get("node_id") or not action.get("model_id"):
        raise ValueError("node_id and model_id are required")
    if action.get("requires_approval") is not True:
        raise ValueError("simulation action must require approval")


def _revalidate(action: dict[str, Any], node: dict[str, Any] | None) -> tuple[bool, str, str]:
    if not node or not node.get("online") or not node.get("report_fresh"):
        return False, "node report is missing, offline, or stale", ""
    ip = str(node.get("ip") or "")
    if not ip:
        return False, "node has no reported IP", ""
    model = str(action.get("model_id") or "")
    loaded = set(node.get("loaded_models") or [])
    available = set(node.get("all_models") or [])
    if action.get("action") == "replicate_candidate":
        if model not in available or model in loaded:
            return False, "model is unavailable or already loaded", ""
    else:
        if model not in loaded:
            return False, "model is no longer loaded", ""
        if len(loaded) <= 1:
            return False, "refusing to unload the node's only resident model", ""
    return True, "", f"http://{ip}:1234/v1"


def _decode(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["action"] = json.loads(result.pop("action_json", "{}") or "{}")
    result["result"] = json.loads(result.pop("result_json", "{}") or "{}")
    return result
