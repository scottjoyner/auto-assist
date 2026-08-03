from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException

from assistx.voice_contract import (
    ADMIN_VOICE_OVERRIDE,
    AUTHENTICATED_SCOTT,
    REGISTERED_USER_UNVERIFIED,
    REJECTED,
    UNKNOWN_SPEAKER,
    CanonicalVoiceEventIn,
    LegacySophiaVoiceEventIn,
    authorize_voice_event,
    canonicalize_legacy_sophia_event,
    normalize_auth_state,
    normalize_voice_event,
    verify_raw_voice_signature,
)


def test_raw_signature_verifies_exact_bytes() -> None:
    raw = b'{"event_id":"evt-1","event_type":"task_created","metadata":{}}'
    digest = hmac.new(b"secret", raw, hashlib.sha256).hexdigest()

    verify_raw_voice_signature(raw, f"sha256={digest}", secret="secret")
    verify_raw_voice_signature(raw, digest, secret="secret")

    with pytest.raises(HTTPException) as exc:
        verify_raw_voice_signature(
            raw + b"\n",
            f"sha256={digest}",
            secret="secret",
        )
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("authenticated_scott", AUTHENTICATED_SCOTT),
        ("admin_voice_override", ADMIN_VOICE_OVERRIDE),
        ("not_scott_known", REGISTERED_USER_UNVERIFIED),
        ("unknown_unverified", UNKNOWN_SPEAKER),
        (None, UNKNOWN_SPEAKER),
        ("made_up", UNKNOWN_SPEAKER),
    ],
)
def test_auth_state_normalization(raw, expected) -> None:
    assert normalize_auth_state(raw) == expected


def test_trusted_action_can_dispatch() -> None:
    decision = authorize_voice_event(
        "task_created",
        AUTHENTICATED_SCOTT,
        auto_dispatch=True,
    )
    assert decision.trusted is True
    assert decision.create_executable_task is True
    assert decision.create_review_task is False
    assert decision.auto_dispatch is True


def test_admin_override_can_dispatch() -> None:
    decision = authorize_voice_event(
        "meeting_transcript",
        ADMIN_VOICE_OVERRIDE,
        auto_dispatch=True,
    )
    assert decision.trusted is True
    assert decision.create_executable_task is True
    assert decision.auto_dispatch is True


@pytest.mark.parametrize(
    "state",
    [UNKNOWN_SPEAKER, REGISTERED_USER_UNVERIFIED],
)
def test_untrusted_action_is_review_only(state: str) -> None:
    decision = authorize_voice_event("task_created", state, auto_dispatch=True)
    assert decision.trusted is False
    assert decision.create_executable_task is False
    assert decision.create_review_task is True
    assert decision.review_required is True
    assert decision.auto_dispatch is False
    assert decision.effective_event_type == "task_proposed"


def test_untrusted_cancellation_cannot_mutate_tasks() -> None:
    decision = authorize_voice_event(
        "cancel_active",
        UNKNOWN_SPEAKER,
        auto_dispatch=True,
    )
    assert decision.allow_cancellation is False
    assert decision.create_review_task is True
    assert decision.effective_event_type == "voice_cancellation_review"


def test_rejected_voice_event_is_audit_only() -> None:
    decision = authorize_voice_event("task_created", REJECTED, auto_dispatch=True)
    assert decision.audit_only is True
    assert decision.create_executable_task is False
    assert decision.create_review_task is False
    assert decision.auto_dispatch is False
    assert decision.effective_event_type == "voice_action_rejected"


def test_missing_actor_never_defaults_to_scott() -> None:
    body = CanonicalVoiceEventIn(
        event_id="evt-missing-actor",
        event_type="task_created",
        text="Build the report",
        metadata={"correlation_id": "corr-1"},
    )
    event = normalize_voice_event(body)
    assert event["actor"]["user_id"] is None
    assert event["actor"]["auth_state"] == UNKNOWN_SPEAKER
    assert event["correlation_id"] == "corr-1"


def test_rich_and_compatibility_fields_preserve_actor_and_links() -> None:
    body = CanonicalVoiceEventIn(
        event_id="evt-rich",
        event_type="task_created",
        text="Update the docs",
        schema_version="2026-06-08.v1",
        correlation_id="550e8400-e29b-41d4-a716-446655440000",
        actor={
            "user_id": "scott",
            "device_id": "desk-mic",
            "auth_state": AUTHENTICATED_SCOTT,
        },
        links={"task_id": "task-1"},
        metadata={},
    )
    event = normalize_voice_event(body)
    assert event["actor"]["user_id"] == "scott"
    assert event["actor"]["device_id"] == "desk-mic"
    assert event["actor"]["auth_state"] == AUTHENTICATED_SCOTT
    assert event["metadata"]["links"]["task_id"] == "task-1"


def test_legacy_endpoint_maps_old_taxonomy_to_canonical_contract() -> None:
    legacy = LegacySophiaVoiceEventIn(
        event_id="evt-legacy",
        event_type="voice_chat",
        transcript_text="Please check the fleet",
        auth_state="unknown_unverified",
        speaker_identity="guest",
        payload={"auto_dispatch": True},
    )
    canonical = canonicalize_legacy_sophia_event(legacy)
    event = normalize_voice_event(canonical)
    decision = authorize_voice_event(
        event["event_type"],
        event["actor"]["auth_state"],
        event["auto_dispatch"],
    )
    assert event["actor"]["auth_state"] == UNKNOWN_SPEAKER
    assert decision.review_required is True
    assert decision.auto_dispatch is False


def test_signature_fixture_matches_sophia_compact_json() -> None:
    payload = {
        "event_id": "evt-fixture",
        "event_type": "task_created",
        "text": "Create a deployment checklist",
        "source": "sophia_voice",
        "session_id": "session-1",
        "client_ts": "1770000000.0",
        "metadata": {
            "auth_state": AUTHENTICATED_SCOTT,
            "correlation_id": "corr-fixture",
            "user_id": "scott",
        },
        "auto_dispatch": True,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(
        b"fixture-secret",
        raw,
        hashlib.sha256,
    ).hexdigest()
    verify_raw_voice_signature(
        raw,
        f"sha256={signature}",
        secret="fixture-secret",
    )
