from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .coordination_metadata import build_event_metadata
from .neo4j_client import Neo4jClient

SCHEMA_VERSION = "2026-06-08.v1"
SUPPORTED_SCHEMA_VERSIONS = {"1.0", "2026-06-08.v1"}
LOW_RISK_ACTIONS = {
    "create_note",
    "draft_text",
    "search_memory",
    "summarize_context",
    "list_tasks",
    "create_draft_task",
    "classify_file",
    "enqueue_ingest_review",
    "local_model_analysis",
}
SCOTT_AUTO_APPROVE_STATES = {"authenticated_scott", "admin_override"}


class EventValidationError(ValueError):
    pass


class EventConflictError(ValueError):
    pass


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _neo4j_props(value: Dict[str, Any]) -> Dict[str, Any]:
    """Convert nested dict/list payloads into JSON sidecar properties.

    Neo4j properties can store primitives and homogeneous primitive lists, but not
    maps or lists of maps. Event and registry payloads commonly contain nested
    objects, so this helper preserves them as `<key>_json` strings.
    """
    out: Dict[str, Any] = {}
    for key, val in (value or {}).items():
        if val is None:
            out[key] = None
        elif isinstance(val, (str, int, float, bool)):
            out[key] = val
        elif isinstance(val, list) and all(isinstance(x, (str, int, float, bool)) or x is None for x in val):
            out[key] = val
        else:
            out[f"{key}_json"] = _json_dumps(val)
    return out


def payload_hash(event: Dict[str, Any]) -> str:
    return hashlib.sha256(_json_dumps(dict(event)).encode("utf-8")).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


def validate_event_envelope(event: Dict[str, Any]) -> Dict[str, Any]:
    required = [
        "event_id",
        "event_type",
        "source_repo",
        "source_service",
        "node_id",
        "occurred_at",
        "idempotency_key",
        "schema_version",
        "subject",
        "payload",
        "artifact_refs",
        "privacy",
    ]
    missing = [field for field in required if field not in event or event[field] in (None, "")]
    if missing:
        raise EventValidationError(f"Missing required event fields: {', '.join(missing)}")
    if str(event.get("schema_version")) not in SUPPORTED_SCHEMA_VERSIONS:
        raise EventValidationError(f"Unsupported schema_version: {event.get('schema_version')}")
    if not isinstance(event.get("subject"), dict) or not event["subject"].get("kind") or not event["subject"].get("id"):
        raise EventValidationError("subject.kind and subject.id are required")
    if not isinstance(event.get("payload"), dict):
        raise EventValidationError("payload must be an object")
    if not isinstance(event.get("artifact_refs"), list):
        raise EventValidationError("artifact_refs must be a list")
    if "metadata" in event and not isinstance(event.get("metadata"), dict):
        raise EventValidationError("metadata must be an object")
    privacy = event.get("privacy")
    if not isinstance(privacy, dict):
        raise EventValidationError("privacy must be an object")
    for field in ("pii", "privacy_class", "retention_class"):
        if field not in privacy:
            raise EventValidationError(f"privacy.{field} is required")
    return event


def action_requires_approval(auth_state: Optional[str], action: Optional[str], risk_level: str = "low") -> bool:
    state = (auth_state or "unknown_speaker").strip().lower()
    action_name = (action or "").strip().lower()
    risk = (risk_level or "low").strip().lower()
    if risk == "high":
        return True
    if state in SCOTT_AUTO_APPROVE_STATES and (not action_name or action_name in LOW_RISK_ACTIONS):
        return False
    return True


def upsert_artifact_refs(neo: Neo4jClient, artifact_refs: List[Dict[str, Any]]) -> List[str]:
    ids: List[str] = []
    if not artifact_refs:
        return ids
    with neo._session() as s:
        for ref in artifact_refs:
            artifact_id = ref.get("artifact_id") or uuid.uuid4().hex
            props = _neo4j_props({**ref, "artifact_id": artifact_id})
            ids.append(artifact_id)
            s.run(
                """
                MERGE (a:ArtifactRef {artifact_id:$artifact_id})
                ON CREATE SET a.created_at=datetime(), a.created_at_ts=timestamp()
                SET a += $props,
                    a.updated_at=datetime(),
                    a.updated_at_ts=timestamp()
                """,
                {"artifact_id": artifact_id, "props": props},
            ).consume()
    return ids


def record_event(neo: Neo4jClient, event: Dict[str, Any]) -> Dict[str, Any]:
    validate_event_envelope(event)
    phash = payload_hash(event)
    event_id = str(event["event_id"])
    idempotency_key = str(event["idempotency_key"])
    payload_json = _json_dumps(event.get("payload") or {})
    subject_json = _json_dumps(event.get("subject") or {})
    artifact_json = _json_dumps(event.get("artifact_refs") or [])
    privacy_json = _json_dumps(event.get("privacy") or {})
    metadata_json = _json_dumps(build_event_metadata(event, event.get("metadata")))
    correlation_id = event.get("correlation_id")
    actor_json = _json_dumps(event.get("actor") or {})
    links_json = _json_dumps(event.get("links") or {})
    with neo._session() as s:
        existing = s.run(
            """
            MATCH (e:EventEnvelope)
            WHERE e.event_id=$event_id OR e.idempotency_key=$idempotency_key
            RETURN e.event_id AS event_id, e.idempotency_key AS idempotency_key, e.payload_hash AS payload_hash
            LIMIT 1
            """,
            {"event_id": event_id, "idempotency_key": idempotency_key},
        ).single()
        if existing:
            if existing["payload_hash"] == phash:
                return {"accepted": True, "event_id": existing["event_id"], "deduped": True, "graph_reconciled": False}
            conflict_id = uuid.uuid4().hex
            s.run(
                """
                CREATE (c:EventConflict {id:$conflict_id, event_id:$event_id, idempotency_key:$idempotency_key,
                    existing_event_id:$existing_event_id, existing_payload_hash:$existing_payload_hash,
                    incoming_payload_hash:$incoming_payload_hash, created_at:datetime(), created_at_ts:timestamp()})
                """,
                {
                    "conflict_id": conflict_id,
                    "event_id": event_id,
                    "idempotency_key": idempotency_key,
                    "existing_event_id": existing["event_id"],
                    "existing_payload_hash": existing["payload_hash"],
                    "incoming_payload_hash": phash,
                },
            ).consume()
            raise EventConflictError(f"Event idempotency conflict for {idempotency_key}")
        s.run(
            """
            CREATE (e:SignalEvent:EventEnvelope {id:$event_id, event_id:$event_id})
            SET e.event_type=$event_type,
                e.source_repo=$source_repo,
                e.source_service=$source_service,
                e.node_id=$node_id,
                e.occurred_at=$occurred_at,
                e.idempotency_key=$idempotency_key,
                e.schema_version=$schema_version,
                e.subject_json=$subject_json,
                e.payload_json=$payload_json,
                e.artifact_refs_json=$artifact_json,
                e.privacy_json=$privacy_json,
                e.metadata_json=$metadata_json,
                e.payload_hash=$payload_hash,
                e.correlation_id=$correlation_id,
                e.actor_json=$actor_json,
                e.links_json=$links_json,
                e.created_at=datetime(),
                e.created_at_ts=timestamp(),
                e.updated_at=datetime(),
                e.updated_at_ts=timestamp()
            """,
            {
                "event_id": event_id,
                "event_type": event["event_type"],
                "source_repo": event["source_repo"],
                "source_service": event["source_service"],
                "node_id": event["node_id"],
                "occurred_at": event["occurred_at"],
                "idempotency_key": idempotency_key,
                "schema_version": event["schema_version"],
                "subject_json": subject_json,
                "payload_json": payload_json,
                "artifact_json": artifact_json,
                "privacy_json": privacy_json,
                "metadata_json": metadata_json,
                "payload_hash": phash,
                "correlation_id": correlation_id,
                "actor_json": actor_json,
                "links_json": links_json,
            },
        ).consume()
    artifact_ids = upsert_artifact_refs(neo, event.get("artifact_refs") or [])
    if artifact_ids:
        with neo._session() as s:
            s.run(
                """
                MATCH (e:EventEnvelope {event_id:$event_id})
                MATCH (a:ArtifactRef)
                WHERE a.artifact_id IN $artifact_ids
                MERGE (e)-[:REFERENCES]->(a)
                """,
                {"event_id": event_id, "artifact_ids": artifact_ids},
            ).consume()
    reconcile_event(neo, event)
    return {"accepted": True, "event_id": event_id, "deduped": False, "graph_reconciled": True}


def reconcile_event(neo: Neo4jClient, event: Dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload") or {}
    if event_type in {"swarm.node.registered", "swarm.node.heartbeat"}:
        upsert_swarm_node(neo, payload if payload else event)
    elif event_type.startswith("router.execution_stage."):
        upsert_route_runtime_sample(neo, payload, event_type)
    elif event_type in {"router.route_decision", "route.selected"}:
        record_route_decision_trace(neo, event, payload)
    elif event_type.startswith("assignment."):
        project_assignment_event(neo, event, payload)
    elif event_type == "model.endpoint.discovered":
        upsert_model_endpoint(neo, payload, event.get("node_id"))
    elif event_type == "voice.auth.decision":
        record_voice_auth_decision(neo, event)
    elif event_type == "voice.quick_input.created":
        record_voice_quick_input(neo, event)
    elif event_type == "ingest.batch.started":
        upsert_ingest_batch(neo, payload, event, status="scanning")
    elif event_type == "ingest.memory_candidate.created":
        upsert_memory_candidate(neo, payload, event)
    elif event_type == "ingest.batch.review_ready":
        upsert_ingest_batch(neo, payload, event, status="reviewing")


def _coerce_timestamp_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return int(numeric * 1000) if abs(numeric) < 10_000_000_000 else int(numeric)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            numeric = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            return int(parsed.timestamp() * 1000)
        return int(numeric * 1000) if abs(numeric) < 10_000_000_000 else int(numeric)
    return None


def project_assignment_event(neo: Neo4jClient, event: Dict[str, Any], payload: Dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or "")
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        return

    assignment_id = payload.get("assignment_id")
    worker_id = payload.get("worker_id")
    node_id = payload.get("node_id")
    claim_id = payload.get("claim_id") or assignment_id
    lease_seconds = payload.get("lease_seconds")
    lease_expires_at_ts = _coerce_timestamp_ms(payload.get("lease_expires_at"))
    last_heartbeat_at_ts = _coerce_timestamp_ms(payload.get("last_heartbeat_at") or event.get("occurred_at"))
    result_json = None
    status = payload.get("status")
    clear_claim_fields = False
    release_reason = None

    if event_type == "assignment.claimed":
        # A claim event canonically transitions the task to CLAIMED; the payload
        # ``status`` (e.g. an assignment-lifecycle value like "running") must not
        # leak into the Task node, which uses the uppercase status vocabulary that
        # downstream lease queries filter on (['CLAIMED','RUNNING']).
        status = "CLAIMED"
    elif event_type == "assignment.heartbeat":
        status = status or "RUNNING"
    elif event_type == "assignment.completed":
        status = status or "DONE"
        result_json = _json_dumps(
            {
                "assignment_id": assignment_id,
                "task_id": task_id,
                "worker_id": worker_id,
                "node_id": node_id,
                "status": payload.get("status"),
                "summary": payload.get("summary"),
                "artifacts": payload.get("artifacts", []),
            }
        )
    elif event_type == "assignment.failed":
        status = status or "FAILED"
        result_json = _json_dumps(
            {
                "assignment_id": assignment_id,
                "task_id": task_id,
                "worker_id": worker_id,
                "node_id": node_id,
                "status": payload.get("status"),
                "summary": payload.get("summary"),
                "artifacts": payload.get("artifacts", []),
            }
        )
    elif event_type == "assignment.released":
        status = "READY"
        clear_claim_fields = True
        release_reason = payload.get("reason") or "released"
    elif event_type == "assignment.expired":
        status = "READY"
        clear_claim_fields = True
        release_reason = payload.get("reason") or "expired"
    else:
        status = status or "CLAIMED"

    with neo._session() as s:
        s.run(
            """
            MATCH (e:SignalEvent:EventEnvelope {event_id:$event_id})
            MERGE (t:Task {id:$task_id})
            ON CREATE SET t.created_at=datetime(), t.created_at_ts=timestamp()
            SET t.updated_at=datetime(),
                t.updated_at_ts=timestamp(),
                t.status=$status,
                t.assignment_id=coalesce($assignment_id, t.assignment_id),
                t.worker_id=coalesce($worker_id, t.worker_id),
                t.node_id=coalesce($node_id, t.node_id),
                t.claim_id=CASE WHEN $clear_claim_fields THEN null ELSE coalesce($claim_id, t.claim_id) END,
                t.claimed_by=CASE WHEN $clear_claim_fields THEN null ELSE coalesce($worker_id, t.claimed_by) END,
                t.agent_session_id=CASE WHEN $clear_claim_fields THEN null ELSE coalesce($agent_session_id, t.agent_session_id) END,
                t.heartbeat_by=CASE WHEN $clear_claim_fields THEN null ELSE coalesce($worker_id, t.heartbeat_by) END,
                t.completed_by=coalesce($worker_id, t.completed_by),
                t.lease_seconds=CASE WHEN $clear_claim_fields THEN null ELSE coalesce($lease_seconds, t.lease_seconds) END,
                t.lease_expires_at_ts=CASE WHEN $clear_claim_fields THEN null ELSE coalesce($lease_expires_at_ts, t.lease_expires_at_ts) END,
                t.last_heartbeat_at_ts=coalesce($last_heartbeat_at_ts, t.last_heartbeat_at_ts),
                t.result_summary=coalesce($result_summary, t.result_summary),
                t.result_json=coalesce($result_json, t.result_json),
                t.lease_released_reason=coalesce($lease_released_reason, t.lease_released_reason)
            MERGE (e)-[:ABOUT_TASK]->(t)
            """,
            {
                "event_id": event.get("event_id"),
                "task_id": task_id,
                "status": status,
                "assignment_id": assignment_id,
                "worker_id": worker_id,
                "node_id": node_id,
                "claim_id": claim_id,
                "agent_session_id": payload.get("correlation_id") or event.get("correlation_id") or assignment_id,
                "lease_seconds": lease_seconds,
                "lease_expires_at_ts": lease_expires_at_ts,
                "last_heartbeat_at_ts": last_heartbeat_at_ts if event_type == "assignment.heartbeat" else None,
                "result_summary": payload.get("summary"),
                "result_json": result_json,
                "lease_released_reason": release_reason,
                "clear_claim_fields": clear_claim_fields,
            },
        ).consume()


def upsert_route_runtime_sample(neo: Neo4jClient, payload: Dict[str, Any], event_type: str) -> Dict[str, Any]:
    node_id = str(payload.get("node_id") or payload.get("provider_id") or "unknown")
    model_id = str(payload.get("provider_model_id") or payload.get("model_id") or payload.get("model") or "").strip()
    execution_status = str(payload.get("status") or "unknown")
    node_status = payload.get("node_status") or ("online" if execution_status == "completed" else "degraded")
    props = _neo4j_props(
        {
            **payload,
            "node_id": node_id,
            "model_id": model_id or None,
            "provider_model_id": payload.get("provider_model_id") or model_id or None,
            "status": node_status,
            "execution_status": execution_status,
            "last_route_event_type": event_type,
            "last_route_provider_id": payload.get("provider_id"),
            "last_route_provider": payload.get("provider"),
            "last_route_model_id": model_id or None,
            "last_route_status": execution_status,
            "last_route_request_id": payload.get("request_id"),
            "last_route_task_id": payload.get("task_id"),
            "last_route_agent_run_id": payload.get("agent_run_id"),
        }
    )
    with neo._session() as s:
        s.run(
            """
            MERGE (n:SwarmNode {node_id:$node_id})
            ON CREATE SET n.created_at=datetime(), n.created_at_ts=timestamp()
            SET n += $props,
                n.last_seen_at=datetime(),
                n.last_seen_at_ts=timestamp(),
                n.updated_at=datetime(),
                n.updated_at_ts=timestamp(),
                n.throughput_tokens_per_second=$tokens_per_second,
                n.queue_wait_ms=$queue_wait_ms,
                n.load_time_ms=$load_time_ms,
                n.value_per_second=$value_per_second,
                n.model_id=coalesce($model_id, n.model_id)
            """,
            {
                "node_id": node_id,
                "props": props,
                "tokens_per_second": payload.get("tokens_per_second"),
                "queue_wait_ms": payload.get("queue_wait_ms"),
                "load_time_ms": payload.get("load_time_ms"),
                "value_per_second": payload.get("value_per_second"),
                "model_id": model_id or None,
            },
        ).consume()
        if model_id:
            model_props = _neo4j_props(
                {
                    "model_id": model_id,
                    "provider": payload.get("provider"),
                    "provider_id": payload.get("provider_id"),
                    "provider_model_id": payload.get("provider_model_id") or model_id,
                }
            )
            s.run(
                """
                MERGE (m:Model {model_id:$model_id})
                ON CREATE SET m.created_at=datetime(), m.created_at_ts=timestamp()
                SET m += $props, m.updated_at=datetime(), m.updated_at_ts=timestamp()
                WITH m
                MATCH (n:SwarmNode {node_id:$node_id})
                MERGE (n)-[:USES_MODEL]->(m)
                """,
                {"node_id": node_id, "model_id": model_id, "props": model_props},
            ).consume()
        rec = s.run("MATCH (n:SwarmNode {node_id:$node_id}) RETURN n", {"node_id": node_id}).single()
        return dict(rec["n"]) if rec else props


def upsert_swarm_node(neo: Neo4jClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    node_id = str(payload.get("node_id") or payload.get("hostname") or "unknown")
    props = _neo4j_props({**payload, "node_id": node_id, "status": payload.get("status") or "online"})
    capabilities = payload.get("capabilities") or []
    services = payload.get("services") or []
    with neo._session() as s:
        s.run(
            """
            MERGE (n:SwarmNode {node_id:$node_id})
            ON CREATE SET n.created_at=datetime(), n.created_at_ts=timestamp()
            SET n += $props,
                n.last_seen_at=datetime(),
                n.last_seen_at_ts=timestamp(),
                n.updated_at=datetime(),
                n.updated_at_ts=timestamp()
            """,
            {"node_id": node_id, "props": props},
        ).consume()
        for cap in capabilities:
            cap_id = cap.get("capability_id") or f"{node_id}.{cap.get('kind','capability')}.{cap.get('name','default')}"
            cap_props = _neo4j_props({**cap, "capability_id": cap_id, "node_id": node_id, "status": cap.get("status") or "available"})
            s.run(
                """
                MATCH (n:SwarmNode {node_id:$node_id})
                MERGE (c:Capability {capability_id:$capability_id})
                ON CREATE SET c.created_at=datetime(), c.created_at_ts=timestamp()
                SET c += $props, c.updated_at=datetime(), c.updated_at_ts=timestamp()
                MERGE (n)-[:CAN_RUN]->(c)
                """,
                {"node_id": node_id, "capability_id": cap_id, "props": cap_props},
            ).consume()
        for svc in services:
            svc_id = svc.get("endpoint_id") or f"{node_id}:{svc.get('service_type','service')}"
            svc_props = _neo4j_props({**svc, "service_type": svc.get("service_type") or "generic", "status": svc.get("status") or "online"})
            s.run(
                """
                MATCH (n:SwarmNode {node_id:$node_id})
                MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})
                ON CREATE SET e.created_at=datetime(), e.created_at_ts=timestamp()
                SET e += $props, e.updated_at=datetime(), e.updated_at_ts=timestamp()
                MERGE (n)-[:EXPOSES]->(e)
                """,
                {"node_id": node_id, "endpoint_id": svc_id, "props": svc_props},
            ).consume()
        rec = s.run("MATCH (n:SwarmNode {node_id:$node_id}) RETURN n", {"node_id": node_id}).single()
        return dict(rec["n"]) if rec else props


def list_swarm_nodes(neo: Neo4jClient, limit: int = 100) -> List[Dict[str, Any]]:
    with neo._session() as s:
        res = s.run(
            """
            MATCH (n:SwarmNode)
            OPTIONAL MATCH (n)-[:CAN_RUN]->(c:Capability)
            OPTIONAL MATCH (n)-[:EXPOSES]->(e:ServiceEndpoint)
            RETURN n, collect(DISTINCT c) AS caps, collect(DISTINCT e) AS endpoints
            ORDER BY coalesce(n.last_seen_at_ts, 0) DESC
            LIMIT $limit
            """,
            {"limit": limit},
        )
        out = []
        for row in res:
            node = dict(row["n"])
            node["capabilities"] = [dict(c) for c in row["caps"] if c]
            node["endpoints"] = [dict(e) for e in row["endpoints"] if e]
            out.append(node)
        return out


def list_capabilities(neo: Neo4jClient, limit: int = 200) -> List[Dict[str, Any]]:
    with neo._session() as s:
        res = s.run(
            "MATCH (c:Capability) RETURN c ORDER BY coalesce(c.kind,''), c.capability_id LIMIT $limit",
            {"limit": limit},
        )
        return [dict(r["c"]) for r in res]


def _endpoint_label(neo: Neo4jClient) -> Optional[str]:
    """Return the active endpoint label if present in the live graph."""
    with neo._session() as s:
        labels = {row["label"] for row in s.run("CALL db.labels() YIELD label RETURN label")}
    if "ModelEndpoint" in labels:
        return "ModelEndpoint"
    if "ServiceEndpoint" in labels:
        return "ServiceEndpoint"
    return None


def _endpoint_id_key(endpoint: Dict[str, Any]) -> str:
    return str(endpoint.get("model_endpoint_id") or endpoint.get("endpoint_id") or "unknown")


def list_model_endpoints(neo: Neo4jClient) -> List[Dict[str, Any]]:
    label = _endpoint_label(neo)
    if not label:
        return []
    id_key = "model_endpoint_id" if label == "ModelEndpoint" else "endpoint_id"
    with neo._session() as s:
        res = s.run(
            f"""
            MATCH (e:{label})
            OPTIONAL MATCH (e)-[:SERVES]->(m:Model)
            RETURN e, collect(DISTINCT m) AS models
            ORDER BY coalesce(e.{id_key}, e.endpoint_id)
            """
        )
        out = []
        for row in res:
            ep = dict(row["e"])
            ep["models"] = [dict(m) for m in row["models"] if m]
            out.append(ep)
        return out


def probe_model_endpoint(neo: Neo4jClient, endpoint: Dict[str, Any]) -> Dict[str, Any]:
    import urllib.request, urllib.error
    base_url = endpoint.get("base_url", "").rstrip("/")
    ep_id = _endpoint_id_key(endpoint)
    node_id = endpoint.get("node_id", "unknown")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    models_url = f"{base_url}/v1/models"
    try:
        req = urllib.request.Request(models_url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
        label = _endpoint_label(neo)
        with neo._session() as s:
            if label:
                id_key = "model_endpoint_id" if label == "ModelEndpoint" else "endpoint_id"
                s.run(
                    f"MATCH (e:{label} {{{id_key}:$ep_id}}) SET e.status='offline', e.probe_error=$err, e.last_probed_at=datetime(), e.last_probed_at_ts=timestamp()",
                    {"ep_id": ep_id, "err": str(e)[:200]},
                ).consume()
        return {"model_endpoint_id": ep_id, "status": "offline", "error": str(e)}

    data = body.get("data") or body.get("models") or []
    if isinstance(data, dict):
        data = [data]
    models = []
    for m in data:
        model_id = m.get("id") or m.get("model") or m.get("name", "unknown")
        models.append({
            "model_id": f"{node_id}.{model_id}",
            "served_name": model_id,
            "family": _infer_model_family(model_id),
            "context_length": _infer_context_length(model_id),
        })

    probe_id = f"probe-{ep_id}-{int(time.time())}"
    event = {
        "event_id": probe_id,
        "event_type": "model.endpoint.discovered",
        "source_repo": "auto-assist",
        "source_service": "model-endpoint-prober",
        "node_id": node_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": probe_id,
        "schema_version": "1.0",
        "subject": {"kind": "model", "id": ep_id},
        "payload": {
            "model_endpoint_id": ep_id,
            "node_id": node_id,
            "base_url": base_url,
            "provider": _infer_provider(endpoint.get("base_url", "")),
            "status": "online",
            "models": models,
        },
        "artifact_refs": [],
        "privacy": {"pii": False, "privacy_class": "public", "retention_class": "keep"},
    }
    record_event(neo, event)
    label = _endpoint_label(neo)
    with neo._session() as s:
        if label:
            id_key = "model_endpoint_id" if label == "ModelEndpoint" else "endpoint_id"
            s.run(
                f"MATCH (e:{label} {{{id_key}:$ep_id}}) SET e.status='online', e.last_probed_at=datetime(), e.last_probed_at_ts=timestamp(), e.probe_error=null",
                {"ep_id": ep_id},
            ).consume()
    return {"model_endpoint_id": ep_id, "status": "online", "models_count": len(models)}


def _infer_provider(base_url: str) -> str:
    url = base_url.lower()
    if "11434" in url or "ollama" in url:
        return "ollama"
    return "lm_studio"


def _infer_model_family(model_id: str) -> str:
    mid = model_id.lower()
    if "qwen" in mid:
        return "qwen"
    if "gemma" in mid:
        return "gemma"
    if "llama" in mid or "lfm" in mid:
        return "lfm"
    if "granite" in mid:
        return "granite"
    if "glm" in mid:
        return "glm"
    if "nomic" in mid or "embed" in mid:
        return "embedding"
    return "unknown"


def _infer_context_length(model_id: str) -> int:
    mid = model_id.lower()
    if "embed" in mid:
        return 8192
    return 32768


def upsert_model_endpoint(neo: Neo4jClient, payload: Dict[str, Any], event_node_id: Optional[str] = None) -> Dict[str, Any]:
    node_id = str(payload.get("node_id") or event_node_id or "unknown")
    endpoint_id = str(payload.get("model_endpoint_id") or payload.get("endpoint_id") or f"{node_id}:model:{payload.get('base_url','unknown')}")
    props = _neo4j_props({**payload, "model_endpoint_id": endpoint_id, "node_id": node_id, "status": payload.get("status") or "online"})
    model_ids: List[str] = []
    with neo._session() as s:
        rec = s.run(
            """
            MERGE (n:SwarmNode {node_id:$node_id})
            ON CREATE SET n.created_at=datetime(), n.created_at_ts=timestamp(), n.status='online'
            SET n.updated_at=datetime(), n.updated_at_ts=timestamp()
            MERGE (e:ModelEndpoint:ServiceEndpoint {model_endpoint_id:$endpoint_id})
            ON CREATE SET e.created_at=datetime(), e.created_at_ts=timestamp()
            SET e += $props,
                e.endpoint_id=coalesce(e.endpoint_id, $endpoint_id),
                e.service_type='model_endpoint',
                e.updated_at=datetime(),
                e.updated_at_ts=timestamp()
            MERGE (n)-[:EXPOSES]->(e)
            RETURN e
            """,
            {"node_id": node_id, "endpoint_id": endpoint_id, "props": props},
        ).single()
        for model in payload.get("models") or []:
            model_id = model.get("model_id") or f"{endpoint_id}:{model.get('served_name') or model.get('id') or model.get('name') or 'unknown'}"
            model_ids.append(model_id)
            s.run(
                """
                MATCH (e:ModelEndpoint {model_endpoint_id:$endpoint_id})
                MERGE (m:Model {model_id:$model_id})
                ON CREATE SET m.created_at=datetime(), m.created_at_ts=timestamp()
                SET m += $props, m.updated_at=datetime(), m.updated_at_ts=timestamp()
                MERGE (e)-[:SERVES]->(m)
                """,
                {"endpoint_id": endpoint_id, "model_id": model_id, "props": _neo4j_props({**model, "model_id": model_id})},
            ).consume()
        if "models" in payload:
            s.run(
                """
                MATCH (e:ModelEndpoint {model_endpoint_id:$endpoint_id})-[r:SERVES]->(m:Model)
                WHERE NOT m.model_id IN $model_ids
                DELETE r
                """,
                {"endpoint_id": endpoint_id, "model_ids": model_ids},
            ).consume()
        return dict(rec["e"]) if rec else props


def delete_model_endpoint(neo: Neo4jClient, model_endpoint_id: str) -> Dict[str, Any]:
    """Delete a model endpoint and its associated Model nodes and relationships."""
    with neo._session() as s:
        # Check existence first
        exists = s.run(
            "MATCH (e:ModelEndpoint {model_endpoint_id:$ep_id}) RETURN count(e) AS cnt",
            {"ep_id": model_endpoint_id},
        ).single()
        if exists["cnt"] == 0:
            return {"deleted": False, "error": f"Model endpoint '{model_endpoint_id}' not found"}

        # Delete the endpoint and all related Model nodes/relationships
        s.run(
            """
            MATCH (e:ModelEndpoint {model_endpoint_id:$ep_id})
            MATCH (e)-[:SERVES]->(m:Model)
            DELETE e, m
            """,
            {"ep_id": model_endpoint_id},
        ).consume()

        # Record deletion event
        event = {
            "event_id": f"delete-{model_endpoint_id}-{int(time.time())}",
            "event_type": "model.endpoint.deleted",
            "source_repo": "auto-assist",
            "source_service": "api",
            "node_id": "system",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "idempotency_key": f"delete-{model_endpoint_id}-{int(time.time())}",
            "schema_version": "1.0",
            "subject": {"kind": "model_endpoint", "id": model_endpoint_id},
            "payload": {"model_endpoint_id": model_endpoint_id},
            "artifact_refs": [],
            "privacy": {"pii": False, "privacy_class": "public", "retention_class": "keep"},
        }
        record_event(neo, event)

    return {"deleted": True, "model_endpoint_id": model_endpoint_id}


def record_voice_auth_decision(neo: Neo4jClient, event: Dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    decision_id = str(payload.get("decision_id") or event["event_id"])
    with neo._session() as s:
        s.run(
            """
            MERGE (v:VoiceAuthDecision {decision_id:$decision_id})
            ON CREATE SET v.created_at=datetime(), v.created_at_ts=timestamp()
            SET v += $props, v.updated_at=datetime(), v.updated_at_ts=timestamp()
            WITH v
            MATCH (e:EventEnvelope {event_id:$event_id})
            MERGE (e)-[:RECORDED_DECISION]->(v)
            """,
            {"decision_id": decision_id, "event_id": event["event_id"], "props": _neo4j_props({**payload, "decision_id": decision_id, "event_id": event["event_id"]})},
        ).consume()
    return decision_id


def record_voice_quick_input(neo: Neo4jClient, event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload") or {}
    text = str(payload.get("text") or "").strip()
    auth_state = str(payload.get("auth_state") or "unknown_speaker")
    action = str(payload.get("action") or "create_draft_task")
    risk_level = str(payload.get("risk_level") or "low")
    approval_required = action_requires_approval(auth_state, action, risk_level)
    intent_id = str(payload.get("intent_id") or f"intent:{event['event_id']}")
    task_id = str(payload.get("task_id") or f"task:{event['event_id']}")
    policy_id = str(payload.get("policy_decision_id") or f"policy:{event['event_id']}")
    task_status = "REVIEW" if approval_required else "READY"
    with neo._session() as s:
        s.run(
            """
            MERGE (i:UserIntent:Intent {id:$intent_id})
            ON CREATE SET i.created_at=datetime(), i.created_at_ts=timestamp()
            SET i.source='voice.quick_input', i.text=$text, i.auth_state=$auth_state,
                i.updated_at=datetime(), i.updated_at_ts=timestamp()
            MERGE (t:Task {id:$task_id})
            ON CREATE SET t.created_at=datetime(), t.created_at_ts=timestamp()
            SET t.title=coalesce($text, 'Voice quick input'),
                t.kind='voice_quick_input',
                t.ticket_type='task',
                t.status=$task_status,
                t.risk_level=$risk_level,
                t.approval_required=$approval_required,
                t.required_capabilities=coalesce($required_capabilities, []),
                t.payload_json=$payload_json,
                t.updated_at=datetime(), t.updated_at_ts=timestamp()
            MERGE (i)-[:TRIGGERED_TASK]->(t)
            MERGE (p:PolicyDecision {decision_id:$policy_id})
            ON CREATE SET p.created_at=datetime(), p.created_at_ts=timestamp()
            SET p.auth_state=$auth_state,
                p.action=$action,
                p.risk_level=$risk_level,
                p.approval_required=$approval_required,
                p.policy='voice_auth_policy_mvp',
                p.updated_at=datetime(), p.updated_at_ts=timestamp()
            MERGE (i)-[:AUTHORIZED_BY]->(p)
            WITH i, t, p
            MATCH (e:EventEnvelope {event_id:$event_id})
            MERGE (e)-[:CREATED_INTENT]->(i)
            MERGE (e)-[:CREATED_TASK]->(t)
            """,
            {
                "intent_id": intent_id,
                "task_id": task_id,
                "policy_id": policy_id,
                "event_id": event["event_id"],
                "text": text,
                "auth_state": auth_state,
                "action": action,
                "risk_level": risk_level,
                "approval_required": approval_required,
                "task_status": task_status,
                "required_capabilities": payload.get("required_capabilities") or [],
                "payload_json": _json_dumps(payload),
            },
        ).consume()
    return {"intent_id": intent_id, "task_id": task_id, "approval_required": approval_required}


def upsert_ingest_batch(neo: Neo4jClient, payload: Dict[str, Any], event: Dict[str, Any], status: str) -> str:
    batch_id = str(payload.get("batch_id") or event.get("subject", {}).get("id") or event["event_id"])
    props = _neo4j_props({**payload, "status": status, "batch_id": batch_id})
    with neo._session() as s:
        s.run(
            """
            MERGE (b:IngestBatch {batch_id:$batch_id})
            ON CREATE SET b.created_at=datetime(), b.created_at_ts=timestamp()
            SET b += $props,
                b.status=$status,
                b.updated_at=datetime(), b.updated_at_ts=timestamp()
            WITH b
            MATCH (e:EventEnvelope {event_id:$event_id})
            MERGE (e)-[:ABOUT_BATCH]->(b)
            """,
            {"batch_id": batch_id, "props": props, "status": status, "event_id": event["event_id"]},
        ).consume()
    return batch_id


def upsert_memory_candidate(neo: Neo4jClient, payload: Dict[str, Any], event: Dict[str, Any]) -> str:
    candidate_id = str(payload.get("candidate_id") or event["event_id"])
    batch_id = str(payload.get("batch_id") or event.get("subject", {}).get("id") or "unknown")
    props = _neo4j_props({**payload, "candidate_id": candidate_id, "batch_id": batch_id, "review_status": payload.get("review_status") or "pending"})
    with neo._session() as s:
        s.run(
            """
            MERGE (b:IngestBatch {batch_id:$batch_id})
            ON CREATE SET b.created_at=datetime(), b.created_at_ts=timestamp(), b.status='reviewing'
            MERGE (m:MemoryCandidate {candidate_id:$candidate_id})
            ON CREATE SET m.created_at=datetime(), m.created_at_ts=timestamp()
            SET m += $props, m.updated_at=datetime(), m.updated_at_ts=timestamp()
            MERGE (b)-[:PRODUCED]->(m)
            WITH m
            MATCH (e:EventEnvelope {event_id:$event_id})
            MERGE (e)-[:CREATED_CANDIDATE]->(m)
            """,
            {"batch_id": batch_id, "candidate_id": candidate_id, "props": props, "event_id": event["event_id"]},
        ).consume()
    return candidate_id


def fail_task(neo: Neo4jClient, task_id: str, agent_id: str, error_summary: str, retryable: bool = True, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    status = "READY" if retryable else "FAILED"
    with neo._session() as s:
        rec = s.run(
            """
            MATCH (t:Task {id:$task_id})
            SET t.status=$status,
                t.failed_by=$agent_id,
                t.agent_session_id=coalesce($session_id, t.agent_session_id),
                t.error_summary=$error_summary,
                t.failure_count=coalesce(t.failure_count, 0) + 1,
                t.last_failed_at=datetime(),
                t.last_failed_at_ts=timestamp(),
                t.updated_at=datetime(),
                t.updated_at_ts=timestamp()
            RETURN t
            """,
            {"task_id": task_id, "status": status, "agent_id": agent_id, "session_id": session_id, "error_summary": error_summary},
        ).single()
        return dict(rec["t"]) if rec else None


def set_task_lease(neo: Neo4jClient, task_id: str, lease_seconds: int = 900) -> None:
    lease_ms = max(1, int(lease_seconds)) * 1000
    expires = _now_ms() + lease_ms
    with neo._session() as s:
        s.run(
            """
            MATCH (t:Task {id:$task_id})
            SET t.lease_expires_at_ts=$expires,
                t.lease_seconds=$lease_seconds,
                t.updated_at=datetime(),
                t.updated_at_ts=timestamp()
            """,
            {"task_id": task_id, "expires": expires, "lease_seconds": int(lease_seconds)},
        ).consume()


def release_expired_task_leases(neo: Neo4jClient, now_ms: Optional[int] = None) -> int:
    now = int(now_ms or _now_ms())
    with neo._session() as s:
        rec = s.run(
            """
            MATCH (t:Task)
            WHERE t.status IN ['CLAIMED','RUNNING'] AND coalesce(t.lease_expires_at_ts, 0) < $now
            SET t.status='READY',
                t.claimed_by=null,
                t.agent_session_id=null,
                t.claim_id=null,
                t.lease_released_reason='expired',
                t.updated_at=datetime(),
                t.updated_at_ts=timestamp()
            RETURN count(t) AS released
            """,
            {"now": now},
        ).single()
        return int(rec["released"] if rec else 0)


# ---------------------------------------------------------------------------
# Trace Event persistence
# ---------------------------------------------------------------------------

TRACE_STATE_ORDER = [
    "assignment.completed",
    "assignment.failed",
    "route.blocked",
    "assignment.heartbeat",
    "assignment.claimed",
    "router.route_decision",
    "route.selected",
    "assignment.requested",
    "dispatch.accepted",
    "dispatch.requested",
    "voice.auth.accepted",
    "voice.auth.rejected",
    "voice.auth.error",
    "voice.auth.requested",
]


def record_route_decision_trace(
    neo: Neo4jClient,
    event: Dict[str, Any],
    payload: Dict[str, Any],
) -> None:
    correlation_id = str(event.get("correlation_id") or payload.get("correlation_id") or "").strip()
    if not correlation_id:
        return
    record_trace_event(
        neo,
        correlation_id=correlation_id,
        event_type=str(event.get("event_type") or "router.route_decision"),
        source=str(event.get("source_repo") or event.get("source_service") or payload.get("source") or "auto-router"),
        task_id=str(payload.get("task_id") or "").strip() or None,
        dispatch_id=str(payload.get("dispatch_id") or "").strip() or None,
        route_id=str(payload.get("route_id") or "").strip() or None,
        payload=payload,
    )


def record_trace_event(
    neo: Neo4jClient,
    *,
    correlation_id: str,
    event_type: str,
    source: str,
    task_id: Optional[str] = None,
    dispatch_id: Optional[str] = None,
    route_id: Optional[str] = None,
    assignment_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    event_id = f"trace:{correlation_id}:{event_type}:{uuid.uuid4().hex[:12]}"
    payload_json = _json_dumps(payload or {})
    with neo._session() as s:
        s.run(
            """
            MERGE (t:TraceEvent {event_id:$event_id})
            ON CREATE SET t.created_at=datetime(), t.created_at_ts=timestamp()
            SET t.correlation_id=$correlation_id,
                t.event_type=$event_type,
                t.source=$source,
                t.task_id=$task_id,
                t.dispatch_id=$dispatch_id,
                t.route_id=$route_id,
                t.assignment_id=$assignment_id,
                t.payload_json=$payload_json,
                t.ts=datetime(),
                t.ts_ms=timestamp()
            """,
            {
                "event_id": event_id,
                "correlation_id": correlation_id,
                "event_type": event_type,
                "source": source,
                "task_id": task_id,
                "dispatch_id": dispatch_id,
                "route_id": route_id,
                "assignment_id": assignment_id,
                "payload_json": payload_json,
            },
        ).consume()
        # Link to correlation group
        s.run(
            """
            MERGE (g:TraceGroup {correlation_id:$correlation_id})
            ON CREATE SET g.created_at=datetime(), g.created_at_ts=timestamp()
            WITH g
            MATCH (t:TraceEvent {event_id:$event_id})
            MERGE (g)-[:HAS_EVENT]->(t)
            """,
            {"event_id": event_id, "correlation_id": correlation_id},
        )
    return event_id


def get_trace(neo: Neo4jClient, correlation_id: str) -> Optional[Dict[str, Any]]:
    with neo._session() as s:
        rows = s.run(
            """
            MATCH (g:TraceGroup {correlation_id:$correlation_id})-[:HAS_EVENT]->(t:TraceEvent)
            RETURN t
            ORDER BY t.ts_ms ASC
            """,
            {"correlation_id": correlation_id},
        )
        events = []
        for row in rows:
            te = dict(row["t"])
            te.pop("created_at", None)
            te.pop("created_at_ts", None)
            events.append(te)
        if not events:
            return None
        current_state = _derive_trace_state(events)
        summary = _build_trace_summary(events)
        return {
            "correlation_id": correlation_id,
            "current_state": current_state,
            "summary": summary,
            "events": events,
        }


def _derive_trace_state(events: List[Dict[str, Any]]) -> str:
    latest_type = events[-1].get("event_type", "") if events else ""
    if latest_type == "assignment.completed":
        return "completed"
    if latest_type == "assignment.failed":
        return "failed"
    if latest_type == "route.blocked":
        return "blocked"
    if latest_type == "assignment.expired":
        return "expired"
    if latest_type == "assignment.released":
        return "pending_assignment"
    if latest_type in ("assignment.heartbeat", "assignment.claimed"):
        return "running"
    if latest_type in ("route.selected", "router.route_decision"):
        return "pending_assignment"
    if latest_type == "dispatch.accepted":
        return "pending_route"
    if latest_type in ("voice.auth.rejected", "dispatch.rejected"):
        return "rejected"
    if latest_type in ("assignment.requested",):
        return "pending_assignment"
    return "pending"


def _build_trace_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for ev in events:
        et = ev.get("event_type", "")
        payload = {}
        try:
            payload = json.loads(ev.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        if et.startswith("voice.auth."):
            summary["voice_auth"] = et.split(".")[-1]
        elif et.startswith("dispatch."):
            summary["dispatch"] = et.split(".")[-1]
        elif et.startswith("route."):
            summary["route"] = et.split(".")[-1]
            if "lane" in payload:
                summary["lane"] = payload["lane"]
            if "model" in payload:
                summary["model"] = payload["model"]
        elif et.startswith("assignment."):
            summary["assignment"] = et.split(".")[-1]
            if "worker_id" in payload:
                summary["worker"] = payload["worker_id"]
            if "node_id" in payload:
                summary["node_id"] = payload["node_id"]
    return summary


# W-29: receiver-side stub for auto-ingest contract events. auto-ingest emits
# ``ingest.evidence.linked`` / ``context.available`` via ``auto_ingest_client``;
# this maps them into the canonical EventEnvelope store so they appear in the
# coordination timeline. Kept intentionally thin until auto-ingest is wired.
_AUTO_INGEST_EVENT_TYPES = {
    "ingest.evidence.linked",
    "context.available",
}


def record_auto_ingest_event(
    neo: Neo4jClient,
    event_type: str,
    payload: Dict[str, Any],
    correlation_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Ingest a single auto-ingest contract event into the coordination store.

    TODO(W-29): once auto-ingest is connected, callers should POST to
    ``/api/events`` instead; this helper remains for tests/internal use.
    """
    if event_type not in _AUTO_INGEST_EVENT_TYPES:
        raise ValueError(f"unsupported auto-ingest event type: {event_type}")
    event = {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "event_type": event_type,
        "source_repo": "auto-ingest",
        "source_service": "auto-ingest",
        "idempotency_key": idempotency_key or f"auto-ingest:{event_type}:{uuid.uuid4().hex}",
        "correlation_id": correlation_id or uuid.uuid4().hex,
        "payload": payload,
        "links": {"correlation_id": correlation_id or uuid.uuid4().hex},
    }
    return record_event(neo, event)
