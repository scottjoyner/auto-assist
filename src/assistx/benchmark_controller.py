from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import requests

from .evaluation_registry import suite_for_family
from .tool_call_reliability import (
    model_identity_fingerprint,
    normalize_tool_call_probe_evidence,
    tool_call_probe_cases,
)

BENCHMARK_CASES: dict[str, list[dict[str, Any]]] = {
    "coding": [
        {"prompt": "Return only the Python expression that sums even integers in values.", "required_terms": ["sum", "values"], "min_chars": 12},
        {"prompt": "Name two conditions a safe database migration rollback must verify.", "required_terms": ["rollback"], "min_chars": 30},
        {"prompt": "Explain why an idempotency key prevents duplicate task creation.", "required_terms": ["duplicate"], "min_chars": 30},
    ],
    "reasoning": [
        {"prompt": "A job takes 12 minutes on 3 equal workers. How many minutes on 6 equal workers? State the answer and assumption.", "required_terms": ["6"], "min_chars": 20},
        {"prompt": "Identify the safer choice: retry a non-idempotent payment or stop for review. Explain briefly.", "required_terms": ["review"], "min_chars": 25},
        {"prompt": "If all loaded models are busy, explain one reason queueing may be safer than loading another model.", "required_terms": ["load"], "min_chars": 25},
    ],
    "extraction": [
        {"prompt": 'From "node=x1 model=qwen tps=22.5", return JSON with node, model, and tps.', "required_terms": ["x1", "qwen", "22.5"], "min_chars": 20},
        {"prompt": 'Extract the status and retry count from "status=failed retries=2".', "required_terms": ["failed", "2"], "min_chars": 10},
        {"prompt": 'Extract only the IP from "router at 100.64.43.123 port 8088".', "required_terms": ["100.64.43.123"], "min_chars": 10},
    ],
    "summarization": [
        {"prompt": "Summarize in one sentence: The node is online, its model is loaded, but throughput fell by half after memory pressure increased.", "required_terms": ["throughput"], "min_chars": 25},
        {"prompt": "Summarize in one sentence: Benchmarks are advisory, require a task claim, and cannot load models.", "required_terms": ["claim"], "min_chars": 20},
        {"prompt": "Summarize in one sentence: Quality rose while latency rose, so routing depends on task priority.", "required_terms": ["quality"], "min_chars": 20},
    ],
    "tool_use": [
        {"prompt": "Return JSON for a tool call named fleet_status with no arguments.", "required_terms": ["fleet_status"], "min_chars": 20},
        {"prompt": "Return JSON for a read-only health check of node xwing.", "required_terms": ["xwing"], "min_chars": 20},
        {"prompt": "State whether deleting a model should require approval and why.", "required_terms": ["approval"], "min_chars": 20},
    ],
    "long_context": [
        {"prompt": ("Remember the marker FLEET-7391. " + "Operational context. " * 120 + "Return only the marker."), "required_terms": ["FLEET-7391"], "min_chars": 10},
        {"prompt": ("The target node is x1-370. " + "Background detail. " * 120 + "Name the target node."), "required_terms": ["x1-370"], "min_chars": 6},
        {"prompt": ("The safety mode is advisory-only. " + "Policy context. " * 120 + "State the safety mode."), "required_terms": ["advisory"], "min_chars": 8},
    ],
}


def _benchmark_result(result: dict[str, Any] | None) -> dict[str, Any]:
    value = result if isinstance(result, dict) else {}
    nested = value.get("result")
    if isinstance(nested, dict) and (
        "quality_score" in nested
        or "tool_call_probe" in nested
        or "validation_passed" in nested
    ):
        return nested
    return value


def _resolve_model_identity(
    neo: Any,
    node_id: str,
    model_id: str,
) -> dict[str, Any] | None:
    session_factory = getattr(neo, "_session", None)
    if not callable(session_factory):
        return None
    with session_factory() as session:
        rows = [
            dict(row)
            for row in session.run(
                """
                MATCH (r:RuntimeInstance)-[:SERVES]->(m:LoadedModelInstance)
                MATCH (ap:AccessPath {
                    runtime_instance_id:r.runtime_instance_id
                })
                MATCH (c:CapacityObservation {
                    runtime_instance_id:r.runtime_instance_id
                })
                WHERE r.node_id = $node_id
                  AND coalesce(r.admitted, false) = true
                  AND toLower(coalesce(r.status, 'unknown'))
                      IN ['online', 'healthy', 'ready']
                  AND coalesce(r.expires_at_ts, 0) > $now_ms
                  AND coalesce(m.admitted, false) = true
                  AND coalesce(m.expires_at_ts, 0) > $now_ms
                  AND coalesce(ap.approved, false) = true
                  AND coalesce(ap.expires_at_ts, 0) > $now_ms
                  AND coalesce(c.approved, false) = true
                  AND coalesce(c.expires_at_ts, 0) > $now_ms
                  AND $model_id IN [
                      coalesce(m.model_key, ''),
                      coalesce(m.model_id, ''),
                      coalesce(m.provider_model, '')
                  ]
                OPTIONAL MATCH (m)-[:INSTANCE_OF]->(a:ModelArtifact)
                RETURN DISTINCT
                       r.runtime_instance_id AS runtime_instance_id,
                       r.runtime_version AS runtime_version,
                       m.model_instance_id AS model_instance_id,
                       coalesce(
                           m.artifact_fingerprint,
                           a.artifact_fingerprint,
                           a.sha256
                       ) AS artifact_fingerprint,
                       coalesce(m.quantization, a.quantization) AS quantization,
                       coalesce(m.context_length, a.context_length) AS context_length,
                       coalesce(m.updated_at_ts, 0) AS updated_at_ts
                ORDER BY updated_at_ts DESC
                LIMIT 2
                """,
                {
                    "node_id": node_id,
                    "model_id": model_id,
                    "now_ms": int(time.time() * 1000),
                },
            )
        ]
    if len(rows) != 1:
        return None
    identity = {
        "node_id": node_id,
        **rows[0],
    }
    fingerprint = model_identity_fingerprint(identity)
    if not fingerprint:
        return None
    identity["fingerprint"] = fingerprint
    return identity


def _projection_generation(neo: Any) -> int:
    session_factory = getattr(neo, "_session", None)
    if not callable(session_factory):
        return 0
    with session_factory() as session:
        row = session.run(
            """
            MATCH (s:FleetProjectionState {name:'canonical'})
            RETURN coalesce(s.generation, 0) AS generation
            LIMIT 1
            """
        ).single()
    return int(row["generation"] if row else 0)


def record_tool_call_probe_evidence(
    neo: Any,
    task: dict[str, Any],
    agent_id: str,
    status: str,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = _payload(task)
    if (
        task.get("kind") != "adaptive_model_benchmark"
        or payload.get("task_family") != "tool_use"
    ):
        return {"recorded": False, "reason": "not_tool_use_benchmark"}

    snapshot = payload.get("model_identity")
    expected_fingerprint = str(
        payload.get("model_identity_fingerprint") or ""
    ).strip()
    if not isinstance(snapshot, dict) or not expected_fingerprint:
        return {"recorded": False, "reason": "model_identity_not_bound"}

    node_id = str(payload.get("target_node") or agent_id or "").strip()
    model_id = str(payload.get("model") or "").strip()
    if not node_id or not model_id or agent_id != node_id:
        return {"recorded": False, "reason": "executor_identity_mismatch"}

    current = _resolve_model_identity(neo, node_id, model_id)
    if (
        current is None
        or current.get("fingerprint") != expected_fingerprint
    ):
        return {"recorded": False, "reason": "model_identity_changed"}

    benchmark = _benchmark_result(result)
    evidence = normalize_tool_call_probe_evidence(
        benchmark.get("tool_call_probe")
    )
    if not evidence:
        return {"recorded": False, "reason": "probe_evidence_missing"}

    generation = _projection_generation(neo)
    if generation <= 0:
        return {"recorded": False, "reason": "projection_generation_missing"}
    not_before_generation = generation + 1
    now_ms = int(time.time() * 1000)
    ttl_seconds = max(
        60,
        int(
            os.getenv(
                "ASSISTX_TOOL_CALL_PROBE_TTL_SECONDS",
                "86400",
            )
        ),
    )
    expires_at_ms = now_ms + ttl_seconds * 1000
    evidence_id = hashlib.sha256(
        (
            f"{node_id}|{model_id}|{expected_fingerprint}|"
            f"{evidence.get('schema_version')}"
        ).encode()
    ).hexdigest()
    quality_score = float(benchmark.get("quality_score") or 0.0)
    quality_floor_passed = bool(
        status == "DONE"
        and benchmark.get("validation_passed") is True
        and evidence.get("passed") is True
    )

    session_factory = getattr(neo, "_session", None)
    if not callable(session_factory):
        return {"recorded": False, "reason": "neo_session_unavailable"}
    with session_factory() as session:
        session.run(
            """
            MERGE (e:ToolCallProbeEvidence {evidence_id:$evidence_id})
            SET e.node_id = $node_id,
                e.model_id = $model_id,
                e.agent_id = $agent_id,
                e.task_id = $task_id,
                e.benchmark_id = $benchmark_id,
                e.model_identity_fingerprint = $identity_fingerprint,
                e.runtime_instance_id = $runtime_instance_id,
                e.runtime_version = $runtime_version,
                e.model_instance_id = $model_instance_id,
                e.artifact_fingerprint = $artifact_fingerprint,
                e.quantization = $quantization,
                e.context_length = $context_length,
                e.schema_version = $schema_version,
                e.passed = $passed,
                e.quality_score = $quality_score,
                e.quality_floor_passed = $quality_floor_passed,
                e.evidence_json = $evidence_json,
                e.observed_at_ts = $observed_at_ts,
                e.expires_at_ts = $expires_at_ts,
                e.not_before_generation = $not_before_generation
            WITH e
            MATCH (m:LoadedModelInstance {
                model_instance_id:$model_instance_id
            })
            MERGE (m)-[:HAS_TOOL_CALL_PROBE_EVIDENCE]->(e)
            """,
            {
                "evidence_id": evidence_id,
                "node_id": node_id,
                "model_id": model_id,
                "agent_id": agent_id,
                "task_id": str(task.get("id") or ""),
                "benchmark_id": str(payload.get("benchmark_id") or ""),
                "identity_fingerprint": expected_fingerprint,
                "runtime_instance_id": current["runtime_instance_id"],
                "runtime_version": current["runtime_version"],
                "model_instance_id": current["model_instance_id"],
                "artifact_fingerprint": current["artifact_fingerprint"],
                "quantization": current["quantization"],
                "context_length": int(current["context_length"]),
                "schema_version": str(evidence.get("schema_version") or ""),
                "passed": bool(evidence.get("passed")),
                "quality_score": quality_score,
                "quality_floor_passed": quality_floor_passed,
                "evidence_json": json.dumps(evidence, sort_keys=True),
                "observed_at_ts": now_ms,
                "expires_at_ts": expires_at_ms,
                "not_before_generation": not_before_generation,
            },
        ).consume()
    return {
        "recorded": True,
        "evidence_id": evidence_id,
        "passed": bool(evidence.get("passed")),
        "quality_floor_passed": quality_floor_passed,
        "not_before_generation": not_before_generation,
        "expires_at_ts": expires_at_ms,
        "model_identity_fingerprint": expected_fingerprint,
    }


class BenchmarkController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled = os.getenv("ASSISTX_BENCHMARK_CONTROLLER_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.max_tasks_per_tick = max(1, int(os.getenv("ASSISTX_BENCHMARK_MAX_TASKS_PER_TICK", "6")))
        self.max_tokens_per_case = max(32, int(os.getenv("ASSISTX_BENCHMARK_MAX_TOKENS", "256")))
        self.cooldown_seconds = max(60, int(os.getenv("ASSISTX_BENCHMARK_COOLDOWN_SECONDS", "21600")))
        self.last_tick_at = 0
        self.last_result: dict[str, Any] = {}

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_tasks_per_tick": self.max_tasks_per_tick,
            "max_tokens_per_case": self.max_tokens_per_case,
            "cooldown_seconds": self.cooldown_seconds,
            "last_tick_at": self.last_tick_at or None,
            "last_result": self.last_result,
            "safety": {"loaded_models_only": True, "auto_load": False, "shell_commands": False, "assistx_claim_required": True},
        }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        self.enabled = bool(enabled)
        return self.status()

    def tick(self, neo: Any, fetch_plan: Callable[[], dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            now = int(time.time())
            if not self.enabled:
                return {"created": 0, "blocked": True, "reason": "controller_disabled", **self.status()}
            if not force and self.last_tick_at and now - self.last_tick_at < self.cooldown_seconds:
                return {"created": 0, "blocked": True, "reason": "cooldown", **self.status()}
            plan = fetch_plan()
            tasks, rejected = [], []
            for request in (plan.get("requests") or [])[: self.max_tasks_per_tick]:
                task = self._task(request, neo=neo)
                (tasks if task else rejected).append(task or request.get("benchmark_id"))
            created = neo.create_tasks_batch(tasks)
            self.last_tick_at = now
            self.last_result = {
                "created": created,
                "considered": len(tasks) + len(rejected),
                "rejected": rejected,
                "task_keys": [task["idempotency_key"] for task in tasks],
            }
            return {**self.last_result, "blocked": False, **self.status()}

    def _task(
        self, request: dict[str, Any], *, neo: Any | None = None
    ) -> dict[str, Any] | None:
        family = str(request.get("task_family") or "")
        node, model = str(request.get("node_id") or ""), str(request.get("model_id") or "")
        if family not in BENCHMARK_CASES or not node or not model or request.get("requires_model_load") is not False or request.get("execution_mode") != "dry_run":
            return None
        suite = suite_for_family(family)
        model_identity = (
            _resolve_model_identity(neo, node, model)
            if family == "tool_use" and neo is not None
            else None
        )
        digest = hashlib.sha256(f"{node}|{model}|{family}".encode()).hexdigest()[:16]
        return {
            "title": f"Benchmark {model} on {node}: {family}",
            "kind": "adaptive_model_benchmark",
            "required_capabilities": ["llm"],
            "target_agent_id": node,
            "priority": "BATCH",
            "preemptible": True,
            "max_migrations": 2,
            "idempotency_key": f"adaptive-benchmark:{digest}",
            "payload": {
                "queue_class": "batch", "benchmark": True,
                "benchmark_id": request.get("benchmark_id"), "task_family": family,
                "suite_id": suite.id if suite else None,
                "model": model, "target_node": node, "cases": BENCHMARK_CASES[family],
                "tool_call_probes": tool_call_probe_cases() if family == "tool_use" else [],
                "model_identity": model_identity,
                "model_identity_fingerprint": (
                    model_identity.get("fingerprint")
                    if model_identity is not None
                    else ""
                ),
                "max_tokens_per_case": self.max_tokens_per_case, "deadline_seconds": 900,
                "allow_model_load": False, "source": "auto-router-benchmark-plan",
            },
        }


def publish_benchmark_outcome(task: dict[str, Any], agent_id: str, status: str, result: dict[str, Any] | None) -> dict[str, Any]:
    payload = _payload(task)
    if task.get("kind") != "adaptive_model_benchmark" and not payload.get("benchmark"):
        return {"published": False, "reason": "not_benchmark"}
    base, token = os.getenv("AUTO_ROUTER_BASE_URL", "").strip().rstrip("/"), os.getenv("AUTO_ROUTER_ADMIN_TOKEN", "")
    if not base or not token:
        return {"published": False, "reason": "router_or_token_not_configured"}
    result = _benchmark_result(result)
    event_id = f"benchmark:{task.get('id')}:{hashlib.sha256(str(result).encode()).hexdigest()[:12]}"
    body = {
        "event_id": event_id, "source": "assistx-benchmark-controller",
        "task_id": str(task.get("id") or uuid.uuid4().hex), "success": status == "DONE",
        "provider": agent_id, "model": payload.get("model"), "node_id": agent_id,
        "tokens_per_second": result.get("tokens_per_second"),
        "validation_passed": result.get("validation_passed"), "privacy_class": "local_only",
        "metadata": {
            "task_family": payload.get("task_family"), "quality_score": result.get("quality_score"),
            "repair_count": 0, "benchmark_id": payload.get("benchmark_id"),
            "case_count": result.get("case_count"),
            "tool_call_probe": normalize_tool_call_probe_evidence(
                result.get("tool_call_probe")
            ),
            "model_identity_fingerprint": payload.get(
                "model_identity_fingerprint"
            ),
        },
    }
    try:
        response = requests.post(f"{base}/api/memory/outcomes", json=body, headers={"X-Admin-Token": token}, timeout=10)
        return {"published": response.status_code in {200, 201, 409}, "status_code": response.status_code}
    except Exception as exc:
        return {"published": False, "reason": str(exc)[:240]}


def _payload(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("payload") or task.get("payload_json") or {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}
