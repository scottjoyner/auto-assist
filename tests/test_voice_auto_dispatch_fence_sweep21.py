"""Hermetic regression: legacy Sophia voice payloads must not self-grant dispatch.

Sweep 21 / auto-assist. All inputs are synthetic in-process values; no socket,
HTTP, Redis, Neo4j, SQLite, DB/WAL, docker, credential file, recovery-hold file
or network mount is read. The root ``tests/conftest.py`` Neo4j integration
fixtures are never requested.
"""

from __future__ import annotations

import pytest

from assistx.voice_contract import (
    AUTHENTICATED_SCOTT,
    CanonicalVoiceEventIn,
    LegacySophiaVoiceEventIn,
    authorize_voice_event,
    canonicalize_legacy_sophia_event,
    normalize_voice_event,
)


def _authorize(payload: dict, *, event_type: str = "intent"):
    """Run the production legacy -> canonical -> authorization pipeline."""
    legacy = LegacySophiaVoiceEventIn(
        event_id="evt-sweep21",
        event_type=event_type,
        transcript_text="Please do the thing",
        auth_state=AUTHENTICATED_SCOTT,
        speaker_identity="scott",
        payload=payload,
    )
    canonical = canonicalize_legacy_sophia_event(legacy)
    event = normalize_voice_event(canonical)
    decision = authorize_voice_event(
        event["event_type"],
        event["actor"]["auth_state"],
        event["auto_dispatch"],
    )
    return event, decision


@pytest.mark.parametrize("payload", [{}, {"auto_dispatch": None}])
def test_absent_dispatch_claim_is_not_an_approval(payload):
    event, decision = _authorize(payload)
    assert event["auto_dispatch"] is False
    assert decision.auto_dispatch is False
    assert decision.policy_action == "record_only"


@pytest.mark.parametrize(
    "claim",
    ["false", "0", "no", "off", "FALSE", 1],
)
def test_non_boolean_dispatch_claim_is_not_an_approval(claim):
    event, decision = _authorize({"auto_dispatch": claim})
    assert event["auto_dispatch"] is False
    assert decision.auto_dispatch is False
    assert decision.policy_action == "record_only"


def test_canonical_field_already_reads_string_denial_as_false():
    """The canonical contract's bool field is the intended semantics."""
    canonical = CanonicalVoiceEventIn(
        event_id="evt-canonical",
        event_type="intent",
        auto_dispatch="false",
    )
    assert canonical.auto_dispatch is False


def test_explicit_boolean_true_still_authorizes_dispatch():
    event, decision = _authorize({"auto_dispatch": True})
    assert event["auto_dispatch"] is True
    assert decision.auto_dispatch is True
    assert decision.policy_action == "auto_dispatch_allowed"


def test_explicit_boolean_false_still_records_only():
    event, decision = _authorize({"auto_dispatch": False})
    assert event["auto_dispatch"] is False
    assert decision.auto_dispatch is False
    assert decision.policy_action == "record_only"
