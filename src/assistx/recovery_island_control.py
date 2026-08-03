from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .controller_runtime import Neo4jControllerStore, controller_instance_id
from .neo4j_client import Neo4jClient
from .recovery_control import Neo4jRecoveryStore
from .recovery_island import (
    RECOVERY_ISLAND_ACTIONS,
    build_recovery_island_runbook,
    sign_recovery_activation,
    sign_recovery_island_runbook,
)

logger = logging.getLogger(__name__)

_CONTROLLER_ID = "recovery-island-dispatcher"


def _json_mapping(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _json_mapping_from_sources(
    env: dict[str, str],
    *,
    value_name: str,
    file_name: str,
) -> dict[str, Any]:
    file_value = str(env.get(file_name) or "").strip()
    if file_value:
        path = Path(file_value).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"configured signing-key file is missing: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"signing-key file must contain a JSON object: {path}")
        return value
    return _json_mapping(str(env.get(value_name) or "{}"))


def _island_request(plan: dict[str, Any]) -> dict[str, Any] | None:
    parameters = plan.get("parameters")
    if not isinstance(parameters, dict):
        return None
    request = parameters.get("recovery_island")
    if not isinstance(request, dict):
        return None
    action = str(request.get("action") or "").strip().lower()
    deployment = str(request.get("deployment") or "").strip()
    if action not in RECOVERY_ISLAND_ACTIONS or not deployment:
        return None
    return {**request, "action": action, "deployment": deployment}


def _next_epoch(neo: Any, *, node_id: str, deployment: str) -> int:
    with neo._session() as session:
        row = session.run(
            """
            MERGE (epoch:RecoveryIslandEpoch {
              node_id:$node_id,
              deployment:$deployment
            })
            ON CREATE SET epoch.value=0
            SET epoch.value=coalesce(epoch.value, 0) + 1,
                epoch.updated_at_ts=timestamp()
            RETURN epoch.value AS value
            """,
            {"node_id": node_id, "deployment": deployment},
        ).single()
    return int(row["value"] if row else 0)


def _approved_island_proposals(neo: Any, limit: int = 100) -> list[dict[str, Any]]:
    store = Neo4jRecoveryStore(neo)
    proposals = store.list(limit=max(1, min(limit, 500)))
    return [
        proposal
        for proposal in proposals
        if proposal.get("status") == "APPROVED"
        and _island_request(proposal.get("plan") or {}) is not None
    ]


class RecoveryIslandDispatcher:
    """Dispatch target-pinned recovery-island tasks under a fenced lease.

    The dispatcher shares AssistX's Neo4j authority. It does not probe or infer
    recovery topology. The proposal must already contain an operator-approved,
    fingerprinted plan and the target Beelink node ID.
    """

    def __init__(
        self,
        neo_factory: Callable[[], Any] = Neo4jClient,
        *,
        instance_id: str | None = None,
        clock: Callable[[], float] = time.time,
        env: dict[str, str] | None = None,
    ) -> None:
        self.neo_factory = neo_factory
        self.instance_id = instance_id or controller_instance_id()
        self.clock = clock
        self.env = env if env is not None else os.environ
        self.lease_seconds = max(
            30,
            int(self.env.get("ASSISTX_RECOVERY_ISLAND_LEASE_SECONDS", "120")),
        )

    def tick(self) -> dict[str, Any]:
        neo = self.neo_factory()
        try:
            now_ms = int(self.clock() * 1000)
            controller_store = Neo4jControllerStore(neo)
            lease = controller_store.acquire(
                _CONTROLLER_ID,
                self.instance_id,
                now_ms=now_ms,
                ttl_ms=self.lease_seconds * 1000,
            )
            if not lease:
                return {"executed": False, "reason": "standby_not_leader"}
            fencing_token = int(lease.get("fencing_token") or 0)
            proposals = _approved_island_proposals(neo)
            outcomes = [
                self._dispatch_one(neo, proposal, fencing_token)
                for proposal in proposals
            ]
            return {
                "executed": True,
                "fencing_token": fencing_token,
                "proposal_count": len(proposals),
                "outcomes": outcomes,
            }
        finally:
            neo.close()

    def _dispatch_one(
        self,
        neo: Any,
        proposal: dict[str, Any],
        fencing_token: int,
    ) -> dict[str, Any]:
        store = Neo4jRecoveryStore(neo)
        proposal_id = str(proposal.get("id") or "")
        plan = proposal.get("plan") or {}
        request = _island_request(plan)
        if not request:
            return {
                "proposal_id": proposal_id,
                "dispatched": False,
                "reason": "not_recovery_island",
            }
        node_id = str(plan.get("node_id") or request.get("node_id") or "").strip()
        if not node_id:
            return self._fail_before_dispatch(
                store,
                proposal_id,
                "recovery_island_node_id_required",
            )
        executing = store.transition(
            proposal_id,
            "APPROVED",
            "EXECUTING",
            f"controller:{self.instance_id}",
            {"controller_fencing_token": fencing_token},
        )
        if not executing:
            return {
                "proposal_id": proposal_id,
                "dispatched": False,
                "reason": "proposal_changed_concurrently",
            }
        try:
            parameters = {
                key: value
                for key, value in request.items()
                if key not in {"action", "deployment", "node_id", "activation"}
            }
            if request["action"] == "activate":
                activation = request.get("activation")
                if not isinstance(activation, dict):
                    activation = self._controller_activation(
                        neo,
                        proposal_id=proposal_id,
                        node_id=node_id,
                        deployment=request["deployment"],
                        bundle_sha256=str(request.get("bundle_sha256") or ""),
                        fencing_token=fencing_token,
                    )
                parameters["activation"] = activation

            runbook = build_recovery_island_runbook(
                action=request["action"],
                node_id=node_id,
                deployment=request["deployment"],
                parameters=parameters,
                proposal_id=proposal_id,
            )
            runbook_keys = _json_mapping_from_sources(
                self.env,
                value_name="ASSISTX_RECOVERY_ISLAND_RUNBOOK_SIGNING_KEYS",
                file_name="ASSISTX_RECOVERY_ISLAND_RUNBOOK_SIGNING_KEYS_FILE",
            )
            key_id = self.env.get(
                "ASSISTX_RECOVERY_ISLAND_RUNBOOK_ACTIVE_KEY_ID",
                "",
            ).strip()
            secret = str(runbook_keys.get(key_id) or "")
            if not key_id or not secret:
                raise ValueError("recovery_island_runbook_signing_key_not_configured")
            signed = sign_recovery_island_runbook(
                runbook,
                key_id=key_id,
                secret=secret,
                ttl_seconds=int(
                    self.env.get(
                        "ASSISTX_RECOVERY_ISLAND_RUNBOOK_TTL_SECONDS",
                        "900",
                    )
                ),
            )
            result = neo.create_task_with_context(
                title=(
                    f"Recovery island: {request['action']} "
                    f"{request['deployment']} on {node_id}"
                ),
                task_type="fleet_recovery",
                status="READY",
                kind=f"recovery_island_{request['action']}",
                required_capabilities=["recovery"],
                target_agent_id=node_id,
                priority="CRITICAL" if request["action"] == "activate" else "HIGH",
                payload={
                    "runbook": signed,
                    "recovery_island_runbook": signed,
                    "proposal_id": proposal_id,
                    "approved_by": proposal.get("approved_by")
                    or proposal.get("last_actor"),
                    "execution_mode": "fenced_recovery_island",
                    "requires_post_verification": True,
                    "controller_fencing_token": fencing_token,
                },
                idempotency_key=f"recovery-island:{proposal_id}",
            )
            task_id = result.get("task_id")
            status = "DISPATCHED" if task_id else "FAILED"
            store.transition(
                proposal_id,
                "EXECUTING",
                status,
                f"controller:{self.instance_id}",
                {
                    "task_id": task_id,
                    "dispatch_id": result.get("dispatch_id"),
                    "controller_fencing_token": fencing_token,
                },
            )
            return {
                "proposal_id": proposal_id,
                "dispatched": bool(task_id),
                "task_id": task_id,
                "dispatch_id": result.get("dispatch_id"),
                "action": request["action"],
                "deployment": request["deployment"],
                "node_id": node_id,
            }
        except Exception as exc:
            store.transition(
                proposal_id,
                "EXECUTING",
                "FAILED",
                f"controller:{self.instance_id}",
                {"reason": str(exc)[:500]},
            )
            return {
                "proposal_id": proposal_id,
                "dispatched": False,
                "reason": str(exc)[:500],
            }

    def _controller_activation(
        self,
        neo: Any,
        *,
        proposal_id: str,
        node_id: str,
        deployment: str,
        bundle_sha256: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        enabled = self.env.get(
            "ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_ENABLED",
            "false",
        ).lower() in {"1", "true", "yes", "on"}
        if not enabled:
            raise ValueError("recovery_island_activation_envelope_required")
        allowed_deployments = {
            value.strip()
            for value in self.env.get(
                "ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_DEPLOYMENTS",
                "assistx-shadow",
            ).split(",")
            if value.strip()
        }
        if deployment not in allowed_deployments:
            raise ValueError("recovery_island_auto_activation_deployment_not_allowed")
        keys = _json_mapping_from_sources(
            self.env,
            value_name="ASSISTX_RECOVERY_ISLAND_ACTIVATION_SIGNING_KEYS",
            file_name="ASSISTX_RECOVERY_ISLAND_ACTIVATION_SIGNING_KEYS_FILE",
        )
        key_id = self.env.get(
            "ASSISTX_RECOVERY_ISLAND_ACTIVATION_ACTIVE_KEY_ID",
            "",
        ).strip()
        secret = str(keys.get(key_id) or "")
        if not key_id or not secret:
            raise ValueError("recovery_island_activation_signing_key_not_configured")
        if len(bundle_sha256) != 64:
            raise ValueError("recovery_island_bundle_sha256_required")
        epoch = _next_epoch(neo, node_id=node_id, deployment=deployment)
        return sign_recovery_activation(
            {
                "target_node_id": node_id,
                "deployment": deployment,
                "bundle_sha256": bundle_sha256,
                "epoch": epoch,
                "fence_proof": (
                    f"assistx-lease:{_CONTROLLER_ID}:"
                    f"{fencing_token}:{proposal_id}"
                ),
            },
            key_id=key_id,
            secret=secret,
            ttl_seconds=int(
                self.env.get(
                    "ASSISTX_RECOVERY_ISLAND_ACTIVATION_TTL_SECONDS",
                    "300",
                )
            ),
        )

    def _fail_before_dispatch(
        self,
        store: Neo4jRecoveryStore,
        proposal_id: str,
        reason: str,
    ) -> dict[str, Any]:
        store.transition(
            proposal_id,
            "APPROVED",
            "FAILED",
            f"controller:{self.instance_id}",
            {"reason": reason},
        )
        return {
            "proposal_id": proposal_id,
            "dispatched": False,
            "reason": reason,
        }


def start_recovery_island_dispatcher(
    neo_factory: Callable[[], Any] = Neo4jClient,
) -> threading.Thread | None:
    enabled = os.getenv(
        "ASSISTX_RECOVERY_ISLAND_DISPATCH_ENABLED",
        "false",
    ).lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    interval = max(
        5,
        int(os.getenv("ASSISTX_RECOVERY_ISLAND_DISPATCH_INTERVAL_SECONDS", "15")),
    )
    dispatcher = RecoveryIslandDispatcher(neo_factory)

    def loop() -> None:
        while True:
            try:
                result = dispatcher.tick()
                if result.get("proposal_count"):
                    logger.info("recovery-island dispatch tick: %s", result)
            except Exception as exc:  # pragma: no cover - resilience loop
                logger.warning("recovery-island dispatcher failed safely: %s", exc)
            time.sleep(interval)

    thread = threading.Thread(
        target=loop,
        name="recovery-island-dispatcher",
        daemon=True,
    )
    thread.start()
    return thread
