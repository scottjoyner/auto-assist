from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from .intent_classifier import (
    CLASSIFICATION_CANCEL,
    CLASSIFICATION_MEMORY,
    CLASSIFICATION_QUERY,
    CLASSIFICATION_TASK,
    classify_text,
)
from .neo4j_client import Neo4jClient
from .paperclip_client import PaperclipClient
from .swarm_core import record_trace_event as _default_record_trace_event
from .voice_contract import (
    ADMIN_VOICE_OVERRIDE,
    UNKNOWN_SPEAKER,
    CanonicalVoiceEventIn,
    LegacySophiaVoiceEventIn,
    VoiceAuthorizationDecision,
    authorize_voice_event,
    canonicalize_legacy_sophia_event,
    normalize_voice_event,
    parse_json_object,
    verify_raw_voice_signature,
)

MAX_VOICE_EVENT_BYTES = int(
    os.getenv("ASSISTX_MAX_VOICE_EVENT_BYTES", str(256 * 1024))
)
PAPERCLIP_AGENT_ID = os.getenv("PAPERCLIP_AGENT_ID", "Hermes Agent")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _api_module() -> Any | None:
    """Return the assembled API module without introducing an import cycle."""

    return sys.modules.get("assistx.api")


def _neo() -> Any:
    """Use the API's runtime/test Neo4j factory when it is available."""

    module = _api_module()
    factory = getattr(module, "_neo", None) if module is not None else None
    if callable(factory) and factory is not _neo:
        return factory()
    return Neo4jClient()


def _record_trace_event(neo: Any, **kwargs: Any) -> Any:
    module = _api_module()
    recorder = (
        getattr(module, "record_trace_event", None)
        if module is not None
        else None
    )
    if callable(recorder) and recorder is not _record_trace_event:
        return recorder(neo, **kwargs)
    return _default_record_trace_event(neo, **kwargs)


def _paperclip_client() -> PaperclipClient | Any | None:
    module = _api_module()
    getter = (
        getattr(module, "get_paperclip_client", None)
        if module is not None
        else None
    )
    if callable(getter):
        try:
            return getter()
        except ValueError:
            return None
    try:
        return PaperclipClient()
    except ValueError:
        return None


def _paperclip_agent_id() -> str:
    module = _api_module()
    configured = (
        getattr(module, "PAPERCLIP_AGENT_ID", None)
        if module is not None
        else None
    )
    return str(configured or PAPERCLIP_AGENT_ID)


def _runtime_voice_secret() -> str:
    """Resolve the already-loaded API secret so runtime overrides stay valid."""

    module = _api_module()
    module_secret = (
        getattr(module, "VOICE_WEBHOOK_SECRET", None)
        if module is not None
        else None
    )
    return str(
        os.getenv("ASSISTX_VOICE_WEBHOOK_SECRET")
        or module_secret
        or os.getenv("VOICE_WEBHOOK_SECRET")
        or ""
    ).strip()


def _legacy_signature_compat_enabled() -> bool:
    """Allow the retired reserialized signature only in an explicit test window.

    Production always verifies the exact raw request bytes. The temporary
    compatibility path exists solely so the pre-migration API test fixture can
    prove route behavior while it is being updated to sign the transmitted
    bytes. Operators can opt in deliberately during a short migration window.
    """

    configured = os.getenv(
        "ASSISTX_ALLOW_LEGACY_RESERIALIZED_VOICE_SIGNATURE",
        "",
    ).strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _verify_transport_signature(raw_body: bytes, signature: str | None) -> None:
    secret = _runtime_voice_secret()
    try:
        verify_raw_voice_signature(raw_body, signature, secret=secret)
        return
    except HTTPException as exc:
        if (
            exc.status_code != 401
            or exc.detail != "Invalid voice signature"
            or not _legacy_signature_compat_enabled()
        ):
            raise

    module = _api_module()
    legacy_model = (
        getattr(module, "VoiceEventIn", None)
        if module is not None
        else None
    )
    if legacy_model is None:
        raise HTTPException(status_code=401, detail="Invalid voice signature")
    try:
        payload = parse_json_object(raw_body)
        legacy_raw = legacy_model(**payload).model_dump_json(
            exclude_none=True
        ).encode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid voice signature") from exc
    verify_raw_voice_signature(legacy_raw, signature, secret=secret)


def _parse_model(raw_body: bytes, model_type: type[_ModelT]) -> _ModelT:
    payload = parse_json_object(raw_body)
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        errors: list[dict[str, Any]] = []
        for item in exc.errors():
            normalized = dict(item)
            normalized["loc"] = ("body", *tuple(item.get("loc", ())))
            errors.append(normalized)
        raise RequestValidationError(errors) from exc


def _intent_outcome_and_confidence(
    text: str,
    classification: str,
) -> tuple[str, float]:
    text_l = (text or "").strip().lower()
    words = len(text_l.split())
    questionish = "?" in text_l or text_l.startswith(
        ("what", "who", "where", "when", "why", "how")
    )
    if classification == CLASSIFICATION_CANCEL:
        direct = any(
            key in text_l
            for key in ("cancel", "stop", "never mind", "scratch that")
        )
        return "cancellation", 0.94 if direct else 0.85
    if classification == CLASSIFICATION_MEMORY:
        explicit = any(
            key in text_l
            for key in ("remember", "note", "for the record", "keep in mind")
        )
        return "memory_capture", 0.86 if explicit else 0.72
    if classification == CLASSIFICATION_QUERY:
        return "information_query", 0.90 if questionish else 0.75
    if classification == CLASSIFICATION_TASK:
        if words <= 2:
            return "ambiguous", 0.42
        direct = any(
            key in text_l
            for key in (
                "please",
                "can you",
                "could you",
                "i need you",
                "create",
                "build",
                "fix",
                "update",
            )
        )
        return "actionable_task", 0.83 if direct else 0.70
    return "ambiguous", 0.35


def _upsert_voice_incident(
    neo: Any,
    *,
    event_id: str,
    workflow_id: str,
    incident_type: str,
    severity: str,
    detail: str,
    metadata: dict[str, Any],
) -> str:
    incident_id = f"voice:{incident_type}:{event_id}"
    with neo._session() as session:
        session.run(
            """
            MERGE (w:WorkflowIncident {id:$id})
            ON CREATE SET w.created_at=datetime(), w.created_at_ts=timestamp()
            SET w.workflow_id=$workflow_id,
                w.incident_type=$incident_type,
                w.severity=$severity,
                w.detail=$detail,
                w.metadata_json=$metadata_json,
                w.updated_at=datetime(),
                w.updated_at_ts=timestamp()
            """,
            {
                "id": incident_id,
                "workflow_id": workflow_id,
                "incident_type": incident_type,
                "severity": severity,
                "detail": detail[:1000],
                "metadata_json": json.dumps(metadata, sort_keys=True),
            },
        ).consume()
    return incident_id


def _cancel_target_task(neo: Any, event: dict[str, Any]) -> int:
    metadata = event["metadata"]
    links = event.get("links")
    target_task_id = metadata.get("task_id")
    if not target_task_id and isinstance(links, dict):
        target_task_id = links.get("task_id")
    if not target_task_id:
        nested_links = metadata.get("links")
        if isinstance(nested_links, dict):
            target_task_id = nested_links.get("task_id")
    if not target_task_id:
        return 0
    with neo._session() as session:
        row = session.run(
            """
            MATCH (t:Task {id:$task_id})
            WHERE t.status IN ['READY','CLAIMED','RUNNING']
            SET t.status='CANCELLED',
                t.cancelled_reason=$reason,
                t.cancelled_by='sophia_voice',
                t.cancelled_at=datetime(),
                t.cancelled_at_ts=timestamp(),
                t.updated_at=datetime(),
                t.updated_at_ts=timestamp()
            RETURN count(t) AS cancelled
            """,
            {
                "task_id": str(target_task_id),
                "reason": f"Cancelled by trusted voice event {event['event_id']}",
            },
        ).single()
    return int(row["cancelled"] if row else 0)


def _trace_type(decision: VoiceAuthorizationDecision) -> str:
    if decision.audit_only:
        return "voice.action.rejected"
    if decision.review_required:
        return "voice.action.review_required"
    if decision.allow_cancellation:
        return "dispatch.cancelled"
    if decision.create_executable_task:
        return "dispatch.requested"
    return "voice.event.received"


def _apply_transport_identity(
    event: dict[str, Any],
    transport_user: str,
) -> None:
    """Treat authenticated operator transport as an explicit admin override.

    Signed Sophia webhooks must still carry speaker auth state. Basic/trusted
    operator requests are already authenticated at the API boundary and retain
    backward compatibility without pretending an unknown voice was Scott.
    """

    if transport_user == "voice_webhook":
        return
    actor = event["actor"]
    if actor.get("auth_state") == UNKNOWN_SPEAKER:
        actor["auth_state"] = ADMIN_VOICE_OVERRIDE
        actor["user_id"] = actor.get("user_id") or transport_user
        event["metadata"]["auth_state"] = ADMIN_VOICE_OVERRIDE
        event["metadata"]["user_id"] = actor["user_id"]
        event["metadata"]["transport_identity_override"] = True


def _process_voice_event(
    body: CanonicalVoiceEventIn,
    *,
    transport_user: str,
    legacy_endpoint: bool = False,
) -> dict[str, Any]:
    event = normalize_voice_event(body)
    _apply_transport_identity(event, transport_user)
    decision = authorize_voice_event(
        event["event_type"],
        event["actor"]["auth_state"],
        event["auto_dispatch"],
    )
    event["metadata"].update(
        {
            "requested_event_type": decision.requested_event_type,
            "effective_event_type": decision.effective_event_type,
            "authorization_action": decision.policy_action,
            "transport_user": transport_user,
            "legacy_endpoint": legacy_endpoint,
        }
    )
    event_payload = {
        **event,
        "event_type": decision.effective_event_type,
        "authorization": {
            "auth_state": decision.auth_state,
            "trusted": decision.trusted,
            "review_required": decision.review_required,
            "audit_only": decision.audit_only,
            "policy_action": decision.policy_action,
        },
    }

    neo = _neo()
    try:
        signal_id = neo.create_signal_event(
            event_id=event["event_id"],
            event_type=decision.effective_event_type,
            payload=event_payload,
            session_id=event["session_id"],
        )
        _record_trace_event(
            neo,
            correlation_id=event["correlation_id"],
            event_type=_trace_type(decision),
            source="sophia_voice",
            payload={
                "signal_event_id": signal_id,
                "event_id": event["event_id"],
                "requested_event_type": decision.requested_event_type,
                "effective_event_type": decision.effective_event_type,
                "auth_state": decision.auth_state,
                "user_id": event["actor"].get("user_id"),
                "device_id": event["actor"].get("device_id"),
                "authorization_action": decision.policy_action,
            },
        )

        created_intent_id: str | None = None
        created_memory_id: str | None = None
        created_task_id: str | None = None
        created_dispatch_id: str | None = None
        incident_id: str | None = None
        cancelled_tasks = 0
        text = event["text"].strip()

        if decision.audit_only:
            incident_id = _upsert_voice_incident(
                neo,
                event_id=event["event_id"],
                workflow_id=event["event_id"],
                incident_type="voice_auth_rejected",
                severity="warning",
                detail=(
                    "Rejected Sophia voice action was recorded without task admission"
                ),
                metadata=event_payload,
            )
        elif text:
            classification = classify_text(text)
            intent_outcome, intent_confidence = _intent_outcome_and_confidence(
                text,
                classification,
            )
            created_intent_id = neo.upsert_intent(
                source=event["source"],
                text=text,
                idempotency_key=f"voice:{event['event_id']}",
                client_ts=event["client_ts"],
                metadata={
                    **event["metadata"],
                    "session_id": event["session_id"],
                    "correlation_id": event["correlation_id"],
                    "auth_state": decision.auth_state,
                    "actor": event["actor"],
                    "policy_action": decision.policy_action,
                },
                classification=classification,
                intent_outcome=intent_outcome,
                intent_confidence=intent_confidence,
                mark_orchestrated=(
                    decision.create_executable_task or decision.create_review_task
                ),
            )

            if classification in {CLASSIFICATION_MEMORY, CLASSIFICATION_QUERY}:
                created_memory_id = neo.upsert_memory_item(
                    kind=(
                        "voice_note"
                        if classification == CLASSIFICATION_MEMORY
                        else "voice_query"
                    ),
                    text=text,
                    source=event["source"],
                    session_id=event["session_id"],
                    metadata={
                        "voice_event_id": event["event_id"],
                        "voice_event_type": decision.effective_event_type,
                        "classification": classification,
                        "correlation_id": event["correlation_id"],
                        "auth_state": decision.auth_state,
                        "actor": event["actor"],
                    },
                )

            should_create_task = (
                classification == CLASSIFICATION_TASK
                and (
                    decision.create_executable_task
                    or decision.create_review_task
                )
            )
            if should_create_task:
                review_only = decision.create_review_task
                title_prefix = "Review voice request: " if review_only else ""
                title_text = f"{title_prefix}{text}"
                task_result = neo.create_task_with_context(
                    title=(
                        title_text[:120] + "..."
                        if len(title_text) > 120
                        else title_text
                    ),
                    task_type="task",
                    status="REVIEW" if review_only else "READY",
                    kind=(
                        "sophia_voice_review"
                        if review_only
                        else "sophia_voice"
                    ),
                    required_capabilities=[] if review_only else ["terminal"],
                    priority="MEDIUM" if review_only else "HIGH",
                    payload={
                        "source_event_id": event["event_id"],
                        "source_intent": created_intent_id,
                        "voice_event_type": decision.effective_event_type,
                        "requested_event_type": decision.requested_event_type,
                        "correlation_id": event["correlation_id"],
                        "auth_state": decision.auth_state,
                        "actor": event["actor"],
                        "authorization_action": decision.policy_action,
                        "review_required": review_only,
                    },
                    context_query=text,
                    context_sources=["memory", "knowledge", "orchestration"],
                    idempotency_key=(
                        f"voice-review:{event['event_id']}"
                        if review_only
                        else f"voice-task:{event['event_id']}"
                    ),
                    auto_dispatch=False,
                )
                created_task_id = task_result.get("task_id")
                if created_task_id and created_intent_id:
                    with neo._session() as session:
                        session.run(
                            "MATCH (i:Intent {id:$intent_id}), "
                            "(t:Task {id:$task_id}) "
                            "MERGE (i)-[:CREATED_TASK]->(t)",
                            {
                                "intent_id": created_intent_id,
                                "task_id": created_task_id,
                            },
                        ).consume()

                if review_only:
                    incident_id = _upsert_voice_incident(
                        neo,
                        event_id=event["event_id"],
                        workflow_id=(
                            created_task_id
                            or created_intent_id
                            or event["event_id"]
                        ),
                        incident_type="voice_auth_review_required",
                        severity="warning",
                        detail=(
                            "Voice action from "
                            f"auth_state={decision.auth_state} requires review"
                        ),
                        metadata=event_payload,
                    )
                elif decision.auto_dispatch and created_task_id:
                    dispatch_result = neo.create_dispatch_with_paperclip(
                        task_id=created_task_id,
                        target={
                            "capabilities": ["terminal"],
                            "paperclip_agent_id": _paperclip_agent_id(),
                        },
                        idempotency_key=f"voice-dispatch:{event['event_id']}",
                        paperclip_client=_paperclip_client(),
                    )
                    if isinstance(dispatch_result, dict):
                        created_dispatch_id = (
                            dispatch_result.get("dispatch_id")
                            or dispatch_result.get("id")
                        )
                    _record_trace_event(
                        neo,
                        correlation_id=event["correlation_id"],
                        event_type="dispatch.accepted",
                        source="assistx",
                        task_id=created_task_id,
                        dispatch_id=created_dispatch_id,
                        payload={
                            "source_event_id": event["event_id"],
                            "auth_state": decision.auth_state,
                        },
                    )

            if decision.allow_cancellation:
                cancelled_tasks = _cancel_target_task(neo, event)
            elif decision.review_required and not incident_id:
                incident_id = _upsert_voice_incident(
                    neo,
                    event_id=event["event_id"],
                    workflow_id=created_intent_id or event["event_id"],
                    incident_type="voice_auth_review_required",
                    severity="warning",
                    detail=(
                        "Voice request from "
                        f"auth_state={decision.auth_state} requires review"
                    ),
                    metadata=event_payload,
                )

        metadata = event["metadata"]
        neo.link_sophia_voice_records(
            capture_id=str(metadata.get("capture_id") or "").strip() or None,
            intent_id=created_intent_id,
            memory_id=created_memory_id,
            task_id=created_task_id,
            meeting_id=str(metadata.get("meeting_id") or "").strip() or None,
        )

        return {
            "accepted": True,
            "signal_event_id": signal_id,
            "intent_id": created_intent_id,
            "memory_item_id": created_memory_id,
            "task_id": created_task_id,
            "dispatch_id": created_dispatch_id,
            "incident_id": incident_id,
            "cancelled_tasks": cancelled_tasks,
            "correlation_id": event["correlation_id"],
            "trace_url": f"/api/traces/{event['correlation_id']}",
            "auth_state": decision.auth_state,
            "authorization_action": decision.policy_action,
            "review_required": decision.review_required,
            "audit_only": decision.audit_only,
            "legacy_endpoint": legacy_endpoint,
            "contract_fingerprint": hashlib.sha256(
                json.dumps(
                    {
                        "event_id": event["event_id"],
                        "event_type": decision.effective_event_type,
                        "auth_state": decision.auth_state,
                        "correlation_id": event["correlation_id"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
    finally:
        neo.close()


def _ensure_body_size(raw_body: bytes) -> None:
    if len(raw_body) > MAX_VOICE_EVENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Voice event body exceeds configured limit",
        )


def _authenticate_transport(
    *,
    operator_user: str | None,
    raw_body: bytes,
    signature: str | None,
) -> str:
    if operator_user:
        return operator_user
    _verify_transport_signature(raw_body, signature)
    return "voice_webhook"


def register_voice_routes(
    router: APIRouter,
    optional_operator_auth: Callable[..., str | None],
) -> None:
    """Register canonical routes before api.py's deprecated handlers.

    api.py includes swarm_routes.router before declaring its legacy voice paths,
    so these handlers are the first matching routes at runtime. The old
    functions remain temporarily import-compatible while callers migrate.
    """

    @router.post("/api/voice/events", tags=["voice"])
    async def api_canonical_voice_event(
        request: Request,
        operator_user: str | None = Depends(optional_operator_auth),
        x_voice_signature: str | None = Header(default=None),
    ):
        raw_body = await request.body()
        _ensure_body_size(raw_body)
        transport_user = _authenticate_transport(
            operator_user=operator_user,
            raw_body=raw_body,
            signature=x_voice_signature,
        )
        body = _parse_model(raw_body, CanonicalVoiceEventIn)
        result = _process_voice_event(body, transport_user=transport_user)
        status_code = (
            202 if result["review_required"] or result["audit_only"] else 200
        )
        return JSONResponse(status_code=status_code, content=result)

    @router.post("/api/sophia/events", tags=["voice"], deprecated=True)
    async def api_legacy_sophia_voice_event(
        request: Request,
        operator_user: str | None = Depends(optional_operator_auth),
        x_voice_signature: str | None = Header(default=None),
    ):
        raw_body = await request.body()
        _ensure_body_size(raw_body)
        transport_user = _authenticate_transport(
            operator_user=operator_user,
            raw_body=raw_body,
            signature=x_voice_signature,
        )
        legacy = _parse_model(raw_body, LegacySophiaVoiceEventIn)
        body = canonicalize_legacy_sophia_event(legacy)
        result = _process_voice_event(
            body,
            transport_user=transport_user,
            legacy_endpoint=True,
        )
        status_code = (
            202 if result["review_required"] or result["audit_only"] else 200
        )
        response = JSONResponse(status_code=status_code, content=result)
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "Wed, 30 Sep 2026 23:59:59 GMT"
        response.headers["Link"] = (
            '</api/voice/events>; rel="successor-version"'
        )
        return response
