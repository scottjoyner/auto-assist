from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLACEHOLDER_FRAGMENTS = (
    "change-me",
    "replace-with",
    "<same-node-secret>",
    "<node-specific",
    "<signing-secret>",
)

OBSERVATION_CHECKS = {"legacy_shell"}
RECOVERY_CHECKS = {
    "control_execution",
    "node_execution",
    "signing_key",
    "verification_key",
    "node_identity",
    "service_allowlist",
    "legacy_shell",
}
IMPROVEMENT_CHECKS = {
    "improvement_repositories",
    "improvement_attestation",
    "improvement_worktrees",
    "legacy_shell",
}
CACHE_CHECKS = {"kv_prefix_identity", "node_identity", "legacy_shell"}


class CanaryFailure(RuntimeError):
    pass


def load_environment_file(path: Path) -> dict[str, str]:
    values = {}
    for number, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ValueError(f"{path}:{number}: invalid environment key")
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def validate_environment(
    env: dict[str, str],
    *,
    stages: set[str],
) -> list[str]:
    required = {
        "BASIC_AUTH_USER",
        "BASIC_AUTH_PASS",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "NEO4J_DATABASE",
        "REDIS_URL",
        "OPENAI_BASE_URL",
        "LLM_MODEL",
    }
    if "cache" in stages:
        required |= {
            "ASSISTX_KV_PREFIX_HMAC_SECRET",
            "ASSISTX_FLEET_NODE_TOKENS",
            "CANARY_NODE_ID",
            "CANARY_NODE_TOKEN",
        }
    if "recovery" in stages:
        required |= {
            "ASSISTX_RUNBOOK_SIGNING_KEYS",
            "ASSISTX_RUNBOOK_ACTIVE_KEY_ID",
            "ASSISTX_FLEET_NODE_TOKENS",
            "CANARY_NODE_ID",
            "CANARY_NODE_TOKEN",
            "CANARY_RECOVERY_NODE_ID",
        }
    if "improvement" in stages:
        required |= {
            "ASSISTX_REPOSITORY_ROOTS_JSON",
            "ASSISTX_IMPROVEMENT_WORKTREE_ROOT",
            "ASSISTX_IMPROVEMENT_VERIFY_KEYS",
            "CANARY_IMPROVEMENT_REPOSITORY",
        }
    if "migration" in stages:
        required |= {
            "CANARY_MIGRATION_SOURCE",
            "CANARY_MIGRATION_DESTINATION",
        }
    errors = []
    for name in sorted(required):
        value = str(env.get(name) or "").strip()
        if not value:
            errors.append(f"{name} is required")
            continue
        if any(fragment in value.lower() for fragment in PLACEHOLDER_FRAGMENTS):
            errors.append(f"{name} still contains a placeholder")
    if str(env.get("FLEET_UNSAFE_SHELL_TASKS_ENABLED", "false")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        errors.append("FLEET_UNSAFE_SHELL_TASKS_ENABLED must remain false")
    if (
        "migration" in stages
        and env.get("CANARY_MIGRATION_SOURCE")
        == env.get("CANARY_MIGRATION_DESTINATION")
    ):
        errors.append("migration source and destination must differ")
    if "recovery" not in stages and str(
        env.get("ASSISTX_RECOVERY_EXECUTION_ENABLED", "false")
    ).lower() in {"1", "true", "yes", "on"}:
        errors.append(
            "recovery execution must remain disabled outside the recovery stage"
        )
    if "cache" in stages or "recovery" in stages:
        node_id = str(env.get("CANARY_NODE_ID") or "")
        node_token = str(env.get("CANARY_NODE_TOKEN") or "")
        try:
            node_tokens = json.loads(
                env.get("ASSISTX_FLEET_NODE_TOKENS", "{}")
            )
        except json.JSONDecodeError:
            node_tokens = {}
            errors.append("ASSISTX_FLEET_NODE_TOKENS must be valid JSON")
        if not isinstance(node_tokens, dict):
            errors.append("ASSISTX_FLEET_NODE_TOKENS must be a JSON object")
        elif node_id and node_token and node_tokens.get(node_id) != node_token:
            errors.append(
                "CANARY_NODE_TOKEN must match the node identity registry"
            )
        recovery_node_id = str(env.get("CANARY_RECOVERY_NODE_ID") or "")
        if (
            "recovery" in stages
            and isinstance(node_tokens, dict)
            and recovery_node_id not in node_tokens
        ):
            errors.append(
                "CANARY_RECOVERY_NODE_ID must exist in the node registry"
            )
    if "cache" in stages and len(
        str(env.get("ASSISTX_KV_PREFIX_HMAC_SECRET") or "")
    ) < 32:
        errors.append("ASSISTX_KV_PREFIX_HMAC_SECRET must be at least 32 chars")
    if "recovery" in stages:
        if str(env.get("ASSISTX_RECOVERY_EXECUTION_ENABLED", "false")).lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            errors.append(
                "ASSISTX_RECOVERY_EXECUTION_ENABLED must be true "
                "for the recovery stage"
            )
        if str(env.get("FLEET_RECOVERY_RUNBOOKS_ENABLED", "false")).lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            errors.append(
                "FLEET_RECOVERY_RUNBOOKS_ENABLED must be true "
                "for recovery readiness"
            )
        active_key = str(env.get("ASSISTX_RUNBOOK_ACTIVE_KEY_ID") or "")
        mappings = {}
        for name in (
            "ASSISTX_RUNBOOK_SIGNING_KEYS",
            "FLEET_RUNBOOK_VERIFY_KEYS",
        ):
            try:
                mappings[name] = json.loads(env.get(name, "{}"))
            except json.JSONDecodeError:
                mappings[name] = {}
                errors.append(f"{name} must be valid JSON")
        signing_value = (mappings["ASSISTX_RUNBOOK_SIGNING_KEYS"] or {}).get(
            active_key
        )
        verify_value = (mappings["FLEET_RUNBOOK_VERIFY_KEYS"] or {}).get(
            active_key
        )
        if not signing_value or signing_value != verify_value:
            errors.append(
                "active recovery signing and verification keys must match"
            )
    if "improvement" in stages:
        repository = str(env.get("CANARY_IMPROVEMENT_REPOSITORY") or "")
        try:
            roots = json.loads(env.get("ASSISTX_REPOSITORY_ROOTS_JSON", "{}"))
        except json.JSONDecodeError:
            roots = {}
            errors.append("ASSISTX_REPOSITORY_ROOTS_JSON must be valid JSON")
        if not isinstance(roots, dict) or repository not in roots:
            errors.append(
                "CANARY_IMPROVEMENT_REPOSITORY must exist in repository roots"
            )
    return errors


def readiness_failures(
    payload: dict[str, Any],
    *,
    stages: set[str],
) -> list[str]:
    expected = set(OBSERVATION_CHECKS)
    if "cache" in stages:
        expected |= CACHE_CHECKS
    if "recovery" in stages:
        expected |= RECOVERY_CHECKS
    if "improvement" in stages:
        expected |= IMPROVEMENT_CHECKS
    checks = {
        str(check.get("id")): check
        for check in payload.get("checks") or []
        if isinstance(check, dict)
    }
    failures = []
    for check_id in sorted(expected):
        check = checks.get(check_id)
        if not check:
            failures.append(f"readiness check {check_id} is missing")
        elif not check.get("ready"):
            failures.append(
                f"{check_id}: {check.get('detail') or 'not ready'}"
            )
    return failures


@dataclass
class ApiClient:
    base_url: str
    user: str
    password: str
    timeout: float = 15.0

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        expected: set[int] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        request_headers = {
            "Accept": "application/json",
            "Authorization": "Basic "
            + base64.b64encode(
                f"{self.user}:{self.password}".encode()
            ).decode(),
            **(headers or {}),
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {"raw": raw.decode(errors="replace")[:2000]}
        allowed = expected or {200}
        if status not in allowed:
            raise CanaryFailure(
                f"{method} {path} returned {status}: {payload}"
            )
        return status, payload


@dataclass
class DeploymentCanary:
    client: ApiClient
    node_id: str
    node_token: str
    stages: set[str]
    improvement_repository: str | None = None
    execute_improvement: bool = False
    execute_recovery: bool = False
    recovery_node_id: str | None = None
    migration_source: str | None = None
    migration_destination: str | None = None
    poll_seconds: int = 180
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    report: dict[str, Any] = field(default_factory=dict)

    def _record(self, name: str, value: Any) -> Any:
        self.report.setdefault("steps", {})[name] = value
        return value

    def run(self) -> dict[str, Any]:
        self.report = {
            "schema_version": 1,
            "run_id": self.run_id,
            "started_at_ts": int(time.time() * 1000),
            "stages": sorted(self.stages),
            "steps": {},
            "ok": False,
        }
        try:
            self.check_health()
            self.check_readiness()
            self.check_control_surfaces()
            self.run_fenced_task()
            if "migration" in self.stages:
                self.run_migration()
            if "cache" in self.stages:
                self.run_cache_catalog()
            if "improvement" in self.stages:
                self.run_improvement()
            if "recovery" in self.stages:
                self.run_recovery()
            self.report["ok"] = True
        except Exception as exc:
            self.report["error"] = str(exc)
            raise
        finally:
            self.report["finished_at_ts"] = int(time.time() * 1000)
        return self.report

    def check_health(self) -> None:
        _, health = self.client.request("GET", "/health")
        dependencies = health.get("dependencies") or {}
        for dependency in ("redis", "neo4j"):
            status = (dependencies.get(dependency) or {}).get("status")
            if status != "ok":
                raise CanaryFailure(f"{dependency} health is {status}")
        if not (health.get("configuration") or {}).get("ok"):
            raise CanaryFailure(
                f"runtime configuration is invalid: "
                f"{(health.get('configuration') or {}).get('issues')}"
            )
        self._record(
            "health",
            {
                "status": health.get("status"),
                "profile": health.get("profile"),
                "dependencies": {
                    key: (value or {}).get("status")
                    for key, value in dependencies.items()
                },
            },
        )

    def check_readiness(self) -> None:
        _, readiness = self.client.request(
            "GET", "/api/fleet/operations-readiness"
        )
        failures = readiness_failures(readiness, stages=self.stages)
        self._record(
            "readiness",
            {
                "legacy_ready": readiness.get("ready"),
                "stage_failures": failures,
                "checks": readiness.get("checks"),
            },
        )
        if failures:
            raise CanaryFailure("; ".join(failures))

    def check_control_surfaces(self) -> None:
        surfaces = {}
        for name, path in (
            ("controllers", "/api/fleet/controllers"),
            ("dashboard", "/api/fleet/dashboard"),
            ("cache", "/api/fleet/kv-cache?active_only=true"),
            ("migrations", "/api/fleet/migrations?limit=10"),
            ("improvement", "/api/fleet/improvement-cycle?limit=10"),
        ):
            _, payload = self.client.request("GET", path)
            if name == "dashboard":
                payload = {
                    "summary": payload.get("summary"),
                    "source_status": payload.get("source_status"),
                    "allocation_candidates": len(
                        (payload.get("allocation_plan") or {}).get(
                            "assignments", []
                        )
                    ),
                }
            surfaces[name] = payload
        self._record("control_surfaces", surfaces)

    def run_fenced_task(self) -> None:
        task_key = f"deployment-canary:{self.run_id}"
        _, created = self.client.request(
            "POST",
            "/api/tasks",
            {
                "title": f"Deployment canary {self.run_id}",
                "kind": "deployment_canary",
                "required_capabilities": ["deployment_canary"],
                "target_agent_id": self.node_id,
                "priority": "HIGH",
                "payload": {"canary_run_id": self.run_id},
                "idempotency_key": task_key,
            },
        )
        task_id = str(created.get("task_id") or "")
        if not task_id:
            raise CanaryFailure("task creation did not return task_id")
        _, claimed = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/claim",
            {
                "agent_id": self.node_id,
                "capabilities": ["deployment_canary"],
                "idempotency_key": f"{task_key}:claim",
                "lease_seconds": 120,
            },
        )
        claim_id = str(claimed.get("claim_id") or "")
        if not claimed.get("claimed") or not claim_id:
            raise CanaryFailure(f"canary task was not claimed: {claimed}")
        self.client.request(
            "POST",
            f"/api/tasks/{task_id}/heartbeat",
            {
                "agent_id": self.node_id,
                "status": "RUNNING",
                "claim_id": claim_id,
                "metadata": {"canary_run_id": self.run_id},
                "lease_seconds": 120,
            },
        )
        stale_status, _ = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/complete",
            {
                "agent_id": self.node_id,
                "status": "DONE",
                "summary": "stale canary completion must be rejected",
                "result": {"canary_run_id": self.run_id},
                "claim_id": "stale-" + claim_id,
            },
            expected={404, 409},
        )
        _, completed = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/complete",
            {
                "agent_id": self.node_id,
                "status": "DONE",
                "summary": "deployment canary completed",
                "result": {"canary_run_id": self.run_id, "ok": True},
                "claim_id": claim_id,
                "idempotency_key": f"{task_key}:complete",
            },
        )
        final_status = (completed.get("task") or {}).get("status")
        if final_status != "DONE":
            raise CanaryFailure(f"canary task completed as {final_status}")
        self._record(
            "fenced_task",
            {
                "task_id": task_id,
                "claim_id": claim_id,
                "stale_completion_status": stale_status,
                "final_status": final_status,
            },
        )

    def run_cache_catalog(self) -> None:
        prefix_id = "prefix-" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"assistx-deployment-canary:{self.run_id}",
        ).hex.ljust(64, "0")
        compatibility = {
            "model_artifact_hash": "canary-artifact-" + ("a" * 32),
            "model_id": "deployment-canary-model",
            "model_quantization": "q4_k_m",
            "kv_k_quantization": "q8_0",
            "kv_v_quantization": "q8_0",
            "tokenizer_hash": "canary-tokenizer-" + ("b" * 32),
            "chat_template_hash": "canary-template-" + ("c" * 32),
            "adapter_hash": None,
            "runtime": "lmstudio",
            "runtime_version": "canary",
            "cache_format_version": "affinity-v1",
            "context_length": 4096,
            "rope_config_hash": "canary-rope-" + ("d" * 32),
        }
        node_headers = {"X-Fleet-Node-Token": self.node_token}
        _, registered = self.client.request(
            "POST",
            "/api/fleet/kv-cache/manifests",
            {
                "cache_id": f"kvc-deployment-{self.run_id}",
                "prefix_id": prefix_id,
                "node_id": self.node_id,
                "endpoint_id": f"{self.node_id}.deployment-canary",
                "model_id": "deployment-canary-model",
                "runtime": "lmstudio",
                "compatibility": compatibility,
                "privacy_scope": "project",
                "scope_id": "assistx-deployment-canary",
                "token_count": 256,
                "bytes": 0,
                "storage_tier": "gpu",
                "portable": False,
                "ttl_seconds": 300,
            },
            headers=node_headers,
        )
        manifest = registered.get("manifest") or {}
        cache_id = str(manifest.get("cache_id") or "")
        if not registered.get("registered") or not cache_id:
            raise CanaryFailure(f"cache manifest was not registered: {registered}")
        if "artifact_ref" in manifest:
            raise CanaryFailure("cache storage locator leaked from API")
        _, event = self.client.request(
            "POST",
            "/api/fleet/kv-cache/events",
            {
                "cache_id": cache_id,
                "node_id": self.node_id,
                "outcome": "HIT",
                "prefix_id": prefix_id,
                "tokens_saved": 256,
                "prefill_ms_saved": 25,
            },
            headers=node_headers,
        )
        if not event.get("manifest"):
            raise CanaryFailure("cache event was not attached to its manifest")
        self._record(
            "cache_catalog",
            {
                "cache_id": cache_id,
                "prefix_id": prefix_id,
                "compatibility_fingerprint": manifest.get(
                    "compatibility_fingerprint"
                ),
                "event_outcome": (event.get("event") or {}).get("outcome"),
            },
        )

    def run_migration(self) -> None:
        source = str(self.migration_source or "")
        destination = str(self.migration_destination or "")
        if not source or not destination or source == destination:
            raise CanaryFailure(
                "migration requires distinct source and destination nodes"
            )
        task_key = f"deployment-migration:{self.run_id}"
        _, created = self.client.request(
            "POST",
            "/api/tasks",
            {
                "title": f"Deployment migration canary {self.run_id}",
                "kind": "deployment_migration_canary",
                "required_capabilities": [],
                "target_agent_id": source,
                "priority": "HIGH",
                "payload": {"canary_run_id": self.run_id},
                "idempotency_key": task_key,
                "preemptible": True,
                "max_migrations": 1,
            },
        )
        task_id = str(created.get("task_id") or "")
        _, source_claim = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/claim",
            {
                "agent_id": source,
                "capabilities": [],
                "idempotency_key": f"{task_key}:source-claim",
                "lease_seconds": 120,
            },
        )
        source_claim_id = str(source_claim.get("claim_id") or "")
        if not source_claim_id:
            raise CanaryFailure(f"source claim failed: {source_claim}")
        self.client.request(
            "POST",
            f"/api/tasks/{task_id}/heartbeat",
            {
                "agent_id": source,
                "status": "RUNNING",
                "claim_id": source_claim_id,
                "lease_seconds": 120,
            },
        )
        _, preempted = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/preempt",
            {
                "reason": "deployment migration canary",
                "target_agent_id": destination,
            },
        )
        if not preempted.get("requested"):
            raise CanaryFailure(f"preemption failed: {preempted}")
        _, checkpointed = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/checkpoint",
            {
                "agent_id": source,
                "claim_id": source_claim_id,
                "checkpoint": {
                    "handler": "deployment_canary",
                    "phase": "source_paused",
                    "run_id": self.run_id,
                },
                "progress": 0.5,
                "estimated_remaining_seconds": 5,
                "pause": True,
            },
        )
        if not checkpointed.get("checkpointed"):
            raise CanaryFailure(f"checkpoint failed: {checkpointed}")
        _, migrated = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/migrate",
            {"target_agent_id": destination},
        )
        if not migrated.get("migrated"):
            raise CanaryFailure(f"migration failed: {migrated}")
        _, destination_claim = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/claim",
            {
                "agent_id": destination,
                "capabilities": [],
                "idempotency_key": f"{task_key}:destination-claim",
                "lease_seconds": 120,
            },
        )
        destination_claim_id = str(destination_claim.get("claim_id") or "")
        if not destination_claim_id:
            raise CanaryFailure(
                f"destination claim failed: {destination_claim}"
            )
        stale_status, _ = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/complete",
            {
                "agent_id": source,
                "status": "DONE",
                "summary": "stale source completion must be rejected",
                "result": {"canary_run_id": self.run_id},
                "claim_id": source_claim_id,
            },
            expected={404, 409},
        )
        _, completed = self.client.request(
            "POST",
            f"/api/tasks/{task_id}/complete",
            {
                "agent_id": destination,
                "status": "DONE",
                "summary": "migration canary completed",
                "result": {"canary_run_id": self.run_id, "ok": True},
                "claim_id": destination_claim_id,
                "idempotency_key": f"{task_key}:complete",
            },
        )
        final_status = (completed.get("task") or {}).get("status")
        if final_status != "DONE":
            raise CanaryFailure(f"migrated task completed as {final_status}")
        _, history = self.client.request(
            "GET", f"/api/fleet/migrations?task_id={task_id}&limit=20"
        )
        if not history.get("events"):
            raise CanaryFailure("migration completed without audit events")
        self._record(
            "migration",
            {
                "task_id": task_id,
                "source": source,
                "destination": destination,
                "source_claim_id": source_claim_id,
                "destination_claim_id": destination_claim_id,
                "stale_source_status": stale_status,
                "final_status": final_status,
                "audit_events": len(history.get("events") or []),
            },
        )

    def run_improvement(self) -> None:
        if not self.improvement_repository:
            raise CanaryFailure(
                "improvement stage requires CANARY_IMPROVEMENT_REPOSITORY"
            )
        _, proposal = self.client.request(
            "POST",
            "/api/fleet/improvement-cycle/proposals",
            {
                "title": f"Deployment improvement canary {self.run_id}",
                "repository": self.improvement_repository,
                "objective": (
                    "Append the current canary run ID to the deployment "
                    "canary fixture without changing application behavior."
                ),
                "allowed_paths": [
                    "tests/fixtures/deployment_canary.txt"
                ],
                "verification_commands": [
                    ["pytest", "-q", "tests/test_deployment_canary.py"]
                ],
                "recommended_tier": "tool-small",
                "priority": "LOW",
                "target_agent_id": self.node_id,
            },
        )
        task_id = str(proposal.get("task_id") or "")
        if not task_id or proposal.get("status") != "PROPOSED":
            raise CanaryFailure(f"improvement proposal failed: {proposal}")
        result = {
            "task_id": task_id,
            "proposal_status": proposal.get("status"),
            "execution_requested": self.execute_improvement,
        }
        if self.execute_improvement:
            self.client.request(
                "POST",
                f"/api/tasks/{task_id}/approve-proposal",
            )
            result["final_task"] = self.wait_for_task(task_id)
            if result["final_task"].get("status") != "DONE":
                raise CanaryFailure(
                    f"improvement task did not finish DONE: {result['final_task']}"
                )
        self._record("improvement", result)

    def run_recovery(self) -> None:
        if not self.execute_recovery:
            raise CanaryFailure(
                "recovery stage requires --execute-recovery; "
                "the script will only issue a signed health_check"
            )
        diagnosis = {
            "diagnosis_id": f"diag-deployment-{self.run_id}",
            "incident_key": f"incident-deployment-{self.run_id}",
            "node_id": self.recovery_node_id or self.node_id,
            "recommended_recovery": {
                "action": "health_check",
                "risk": "low",
                "verify_after": ["service_online"],
                "rollback": "none",
            },
        }
        _, proposal = self.client.request(
            "POST",
            "/api/fleet/recovery-control/proposals",
            {"diagnosis": diagnosis},
        )
        proposal_id = str(proposal.get("id") or "")
        fingerprint = str(proposal.get("fingerprint") or "")
        if not proposal_id or not fingerprint:
            raise CanaryFailure(f"recovery proposal failed: {proposal}")
        self.client.request(
            "POST",
            f"/api/fleet/recovery-control/proposals/{proposal_id}/approve",
            {"fingerprint": fingerprint},
        )
        _, dispatched = self.client.request(
            "POST",
            f"/api/fleet/recovery-control/proposals/{proposal_id}/execute",
        )
        task_id = str(dispatched.get("task_id") or "")
        if not dispatched.get("executed") or not task_id:
            raise CanaryFailure(f"recovery dispatch failed: {dispatched}")
        final_task = self.wait_for_task(task_id)
        if final_task.get("status") != "DONE":
            raise CanaryFailure(
                f"recovery health check did not finish DONE: {final_task}"
            )
        _, evidence = self.client.request(
            "GET",
            f"/api/fleet/recovery-control/proposals/{proposal_id}/evidence",
        )
        self._record(
            "recovery",
            {
                "proposal_id": proposal_id,
                "task_id": task_id,
                "task_status": final_task.get("status"),
                "proposal_status": (
                    evidence.get("proposal") or {}
                ).get("status"),
                "audit_events": len(evidence.get("audit") or []),
            },
        )

    def wait_for_task(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.poll_seconds
        last = {}
        while time.monotonic() < deadline:
            _, payload = self.client.request("GET", f"/api/tasks/{task_id}")
            last = payload.get("task") or {}
            if last.get("status") in {"DONE", "FAILED", "CANCELLED"}:
                return last
            time.sleep(2)
        raise CanaryFailure(
            f"task {task_id} did not finish within {self.poll_seconds}s; "
            f"last={last}"
        )


def _parse_stages(value: str) -> set[str]:
    stages = {
        item.strip().lower()
        for item in value.split(",")
        if item.strip()
    }
    unknown = stages - {
        "observe",
        "cache",
        "migration",
        "improvement",
        "recovery",
    }
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unsupported stages: {', '.join(sorted(unknown))}"
        )
    return stages or {"observe"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the staged AssistX deployment canary."
    )
    parser.add_argument(
        "--validate-env",
        action="store_true",
        help="validate the process environment and exit",
    )
    parser.add_argument(
        "--stages",
        default=None,
        type=_parse_stages,
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--base-url",
        default=None,
    )
    parser.add_argument(
        "--user",
        default=None,
    )
    parser.add_argument(
        "--password-env",
        default="BASIC_AUTH_PASS",
    )
    parser.add_argument(
        "--node-id",
        default=None,
    )
    parser.add_argument(
        "--node-token-env",
        default="CANARY_NODE_TOKEN",
    )
    parser.add_argument(
        "--improvement-repository",
        default=None,
    )
    parser.add_argument(
        "--migration-source",
        default=None,
    )
    parser.add_argument(
        "--migration-destination",
        default=None,
    )
    parser.add_argument(
        "--recovery-node-id",
        default=None,
    )
    parser.add_argument("--execute-improvement", action="store_true")
    parser.add_argument("--execute-recovery", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=180)
    parser.add_argument("--evidence", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.env_file:
        os.environ.update(load_environment_file(args.env_file))
    stages = set(
        args.stages
        or _parse_stages(os.getenv("CANARY_STAGES", "observe,cache"))
    )
    errors = validate_environment(dict(os.environ), stages=stages)
    if errors:
        for error in errors:
            print(f"[deployment-canary] ERROR: {error}", file=sys.stderr)
        return 2
    if args.validate_env:
        print(
            "[deployment-canary] environment valid for "
            + ",".join(sorted(stages))
        )
        return 0
    password = os.getenv(args.password_env, "")
    node_token = os.getenv(args.node_token_env, "")
    canary = DeploymentCanary(
        ApiClient(
            args.base_url
            or os.getenv("CANARY_BASE_URL", "http://127.0.0.1:18000"),
            args.user or os.getenv("BASIC_AUTH_USER", ""),
            password,
        ),
        node_id=args.node_id
        or os.getenv("CANARY_NODE_ID", "deployment-canary"),
        node_token=node_token,
        stages=stages,
        improvement_repository=args.improvement_repository
        or os.getenv("CANARY_IMPROVEMENT_REPOSITORY"),
        execute_improvement=args.execute_improvement,
        execute_recovery=args.execute_recovery,
        recovery_node_id=args.recovery_node_id
        or os.getenv("CANARY_RECOVERY_NODE_ID"),
        migration_source=args.migration_source
        or os.getenv("CANARY_MIGRATION_SOURCE"),
        migration_destination=args.migration_destination
        or os.getenv("CANARY_MIGRATION_DESTINATION"),
        poll_seconds=args.poll_seconds,
    )
    try:
        report = canary.run()
    except Exception as exc:
        report = canary.report
        print(f"[deployment-canary] FAIL: {exc}", file=sys.stderr)
        exit_code = 1
    else:
        print(f"[deployment-canary] PASS: run_id={canary.run_id}")
        exit_code = 0
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(output + "\n")
        print(f"[deployment-canary] evidence={args.evidence}")
    else:
        print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
