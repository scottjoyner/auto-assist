from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

AUTHENTICATED_SCOTT = "authenticated_scott"
UNKNOWN_SPEAKER = "unknown_speaker"
REGISTERED_USER_UNVERIFIED = "registered_user_unverified"
ADMIN_VOICE_OVERRIDE = "admin_voice_override"
REJECTED = "rejected"

CANONICAL_AUTH_STATES = frozenset(
    {
        AUTHENTICATED_SCOTT,
        UNKNOWN_SPEAKER,
        REGISTERED_USER_UNVERIFIED,
        ADMIN_VOICE_OVERRIDE,
        REJECTED,
    }
)
TRUSTED_AUTH_STATES = frozenset({AUTHENTICATED_SCOTT, ADMIN_VOICE_OVERRIDE})
LEGACY_AUTH_STATE_MAP = {
    "not_scott_known": REGISTERED_USER_UNVERIFIED,
    "unknown_unverified": UNKNOWN_SPEAKER,
}

EXECUTABLE_ACTION_EVENT_TYPES = frozenset(
    {
        "task_created",
        "meeting_transcript",
        "intent",
        "voice_chat",
        "meeting_action_items",
        "ralph_iteration",
    }
)
CANCELLATION_EVENT_TYPES = frozenset(
    {
        "cancel_active",
        "task_cancelled",
        "barge_in",
    }
)
REVIEW_EVENT_TYPES = frozenset(
    {
        "task_proposed",
        "meeting_transcript_review",
        "voice_cancellation_review",
    }
)
AUDIT_ONLY_EVENT_TYPES = frozenset({"voice_action_rejected"})


class CanonicalVoiceEventIn(BaseModel):
    """Canonical AssistX voice event with compatibility fields.

    Sophia's compact wire shape and the richer EventEnvelope-style shape are
    both accepted. Identity and trace fields may arrive top-level or inside
    metadata during the migration window.
    """

    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(min_length=1, max_length=300)
    event_type: str = Field(min_length=1, max_length=200)
    text: str | None = Field(default=None, max_length=100_000)
    source: str = Field(default="sophia_voice", min_length=1, max_length=200)
    session_id: str | None = Field(default=None, max_length=500)
    client_ts: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    auto_dispatch: bool = True
    schema_version: str | None = Field(default=None, max_length=100)
    correlation_id: str | None = Field(default=None, max_length=300)
    actor: dict[str, Any] | None = None
    links: dict[str, Any] | list[dict[str, Any]] | None = None


class LegacySophiaVoiceEventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(min_length=1, max_length=300)
    event_type: str = Field(min_length=1, max_length=200)
    session_id: str | None = Field(default=None, max_length=500)
    transcript_text: str | None = Field(default=None, max_length=100_000)
    auth_state: str | None = Field(default=None, max_length=100)
    speaker_identity: str | None = Field(default=None, max_length=500)
    speaker_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    policy_version: str | None = Field(default=None, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class VoiceAuthorizationDecision:
    auth_state: str
    requested_event_type: str
    effective_event_type: str
    trusted: bool
    auto_dispatch: bool
    create_executable_task: bool
    create_review_task: bool
    allow_cancellation: bool
    review_required: bool
    audit_only: bool
    policy_action: str


def configured_voice_webhook_secret() -> str:
    return (
        os.getenv("ASSISTX_VOICE_WEBHOOK_SECRET")
        or os.getenv("VOICE_WEBHOOK_SECRET")
        or ""
    ).strip()


def verify_raw_voice_signature(
    raw_body: bytes,
    signature: str | None,
    *,
    secret: str | None = None,
) -> None:
    """Verify the HMAC over the exact HTTP body bytes received by AssistX."""

    resolved_secret = (
        secret if secret is not None else configured_voice_webhook_secret()
    ).strip()
    if not resolved_secret:
        raise HTTPException(status_code=503, detail="Voice webhook secret not configured")
    if not signature:
        raise HTTPException(
            status_code=401,
            detail="Missing voice signature header (X-Voice-Signature)",
        )
    digest = hmac.new(
        resolved_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    accepted = {digest, f"sha256={digest}"}
    if not any(hmac.compare_digest(signature, candidate) for candidate in accepted):
        raise HTTPException(status_code=401, detail="Invalid voice signature")


def parse_json_object(raw_body: bytes) -> dict[str, Any]:
    try:
        decoded = raw_body.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Voice event body must be valid UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="Voice event body must be a JSON object")
    return value


def parse_canonical_voice_event(raw_body: bytes) -> CanonicalVoiceEventIn:
    try:
        return CanonicalVoiceEventIn.model_validate(parse_json_object(raw_body))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid canonical voice event") from exc


def parse_legacy_sophia_event(raw_body: bytes) -> LegacySophiaVoiceEventIn:
    try:
        return LegacySophiaVoiceEventIn.model_validate(parse_json_object(raw_body))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid legacy Sophia voice event") from exc


def normalize_auth_state(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    normalized = LEGACY_AUTH_STATE_MAP.get(normalized, normalized)
    if normalized in CANONICAL_AUTH_STATES:
        return normalized
    return UNKNOWN_SPEAKER


def _link_value(links: Any, key: str) -> Any:
    if isinstance(links, Mapping):
        return links.get(key)
    if isinstance(links, list):
        for item in links:
            if not isinstance(item, Mapping):
                continue
            if item.get("rel") == key:
                return item.get("id") or item.get("value") or item.get("href")
            if key in item:
                return item.get(key)
    return None


def normalize_voice_event(body: CanonicalVoiceEventIn) -> dict[str, Any]:
    metadata = dict(body.metadata or {})
    actor = dict(body.actor or {})
    metadata_actor = metadata.get("actor")
    if isinstance(metadata_actor, Mapping):
        for key, value in metadata_actor.items():
            actor.setdefault(str(key), value)

    auth_state = normalize_auth_state(
        actor.get("auth_state")
        or metadata.get("auth_state")
        or metadata.get("speaker_auth_state")
    )
    user_id = actor.get("user_id") or metadata.get("user_id")
    device_id = actor.get("device_id") or metadata.get("device_id")
    links = body.links if body.links is not None else metadata.get("links")
    correlation_id = (
        body.correlation_id
        or metadata.get("correlation_id")
        or _link_value(links, "correlation_id")
        or f"corr:{body.event_id}"
    )

    metadata.setdefault("auth_state", auth_state)
    metadata.setdefault("correlation_id", correlation_id)
    if body.schema_version is not None:
        metadata.setdefault("schema_version", body.schema_version)
    if user_id is not None:
        metadata.setdefault("user_id", user_id)
    if device_id is not None:
        metadata.setdefault("device_id", device_id)
    if links is not None:
        metadata.setdefault("links", links)

    return {
        "event_id": body.event_id,
        "event_type": body.event_type.strip().lower(),
        "text": body.text or "",
        "source": body.source,
        "session_id": body.session_id,
        "client_ts": body.client_ts,
        "metadata": metadata,
        "auto_dispatch": bool(body.auto_dispatch),
        "schema_version": body.schema_version or metadata.get("schema_version"),
        "correlation_id": str(correlation_id),
        "actor": {
            "user_id": (
                str(user_id).strip()
                if user_id is not None and str(user_id).strip()
                else None
            ),
            "device_id": (
                str(device_id).strip()
                if device_id is not None and str(device_id).strip()
                else None
            ),
            "auth_state": auth_state,
        },
        "links": links,
    }


def canonicalize_legacy_sophia_event(
    body: LegacySophiaVoiceEventIn,
) -> CanonicalVoiceEventIn:
    metadata = dict(body.metadata or {})
    metadata.update(
        {
            "auth_state": normalize_auth_state(body.auth_state),
            "speaker_identity": body.speaker_identity,
            "speaker_confidence": body.speaker_confidence,
            "policy_version": body.policy_version,
            "legacy_sophia_endpoint": True,
        }
    )
    metadata = {key: value for key, value in metadata.items() if value is not None}
    actor = {
        "user_id": body.speaker_identity,
        "auth_state": normalize_auth_state(body.auth_state),
    }
    auto_dispatch = bool(body.payload.get("auto_dispatch", True))
    return CanonicalVoiceEventIn(
        event_id=body.event_id,
        event_type=body.event_type,
        text=body.transcript_text,
        source="sophia_voice",
        session_id=body.session_id,
        metadata=metadata,
        auto_dispatch=auto_dispatch,
        actor=actor,
    )


def authorize_voice_event(
    event_type: str,
    auth_state: Any,
    auto_dispatch: bool,
) -> VoiceAuthorizationDecision:
    requested = str(event_type or "").strip().lower()
    state = normalize_auth_state(auth_state)
    trusted = state in TRUSTED_AUTH_STATES

    if state == REJECTED or requested in AUDIT_ONLY_EVENT_TYPES:
        return VoiceAuthorizationDecision(
            auth_state=state,
            requested_event_type=requested,
            effective_event_type="voice_action_rejected",
            trusted=False,
            auto_dispatch=False,
            create_executable_task=False,
            create_review_task=False,
            allow_cancellation=False,
            review_required=False,
            audit_only=True,
            policy_action="rejected_audit_only",
        )

    if requested in REVIEW_EVENT_TYPES:
        return VoiceAuthorizationDecision(
            auth_state=state,
            requested_event_type=requested,
            effective_event_type=requested,
            trusted=trusted,
            auto_dispatch=False,
            create_executable_task=False,
            create_review_task=True,
            allow_cancellation=False,
            review_required=True,
            audit_only=False,
            policy_action="review_required",
        )

    if requested in CANCELLATION_EVENT_TYPES:
        if trusted:
            return VoiceAuthorizationDecision(
                auth_state=state,
                requested_event_type=requested,
                effective_event_type=requested,
                trusted=True,
                auto_dispatch=False,
                create_executable_task=False,
                create_review_task=False,
                allow_cancellation=True,
                review_required=False,
                audit_only=False,
                policy_action="cancellation_allowed",
            )
        return VoiceAuthorizationDecision(
            auth_state=state,
            requested_event_type=requested,
            effective_event_type="voice_cancellation_review",
            trusted=False,
            auto_dispatch=False,
            create_executable_task=False,
            create_review_task=True,
            allow_cancellation=False,
            review_required=True,
            audit_only=False,
            policy_action="review_required",
        )

    if requested in EXECUTABLE_ACTION_EVENT_TYPES:
        if trusted:
            return VoiceAuthorizationDecision(
                auth_state=state,
                requested_event_type=requested,
                effective_event_type=requested,
                trusted=True,
                auto_dispatch=bool(auto_dispatch),
                create_executable_task=True,
                create_review_task=False,
                allow_cancellation=False,
                review_required=False,
                audit_only=False,
                policy_action=(
                    "auto_dispatch_allowed" if auto_dispatch else "record_only"
                ),
            )
        effective = (
            "meeting_transcript_review"
            if requested in {"meeting_transcript", "meeting_action_items"}
            else "task_proposed"
        )
        return VoiceAuthorizationDecision(
            auth_state=state,
            requested_event_type=requested,
            effective_event_type=effective,
            trusted=False,
            auto_dispatch=False,
            create_executable_task=False,
            create_review_task=True,
            allow_cancellation=False,
            review_required=True,
            audit_only=False,
            policy_action="review_required",
        )

    return VoiceAuthorizationDecision(
        auth_state=state,
        requested_event_type=requested,
        effective_event_type=requested,
        trusted=trusted,
        auto_dispatch=False,
        create_executable_task=False,
        create_review_task=False,
        allow_cancellation=False,
        review_required=False,
        audit_only=False,
        policy_action="record_only",
    )
