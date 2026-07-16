"""Contract tests for the shared fleet envelope.

Run by every repo that imports ``assistx.contracts`` to guarantee the canonical
envelope behaves identically across the fleet.
"""

from __future__ import annotations

import uuid

import pytest

from ..event_envelope import (
    Actor,
    AuthState,
    EventEnvelope,
    EventLink,
    TraceEvent,
    TraceGroup,
)
from ..version import SCHEMA_VERSION


def _corr() -> str:
    return uuid.uuid4().hex


def test_schema_version_is_pinned() -> None:
    assert SCHEMA_VERSION == "2026-06-08.v1"


def test_envelope_requires_correlation_id() -> None:
    with pytest.raises(Exception):
        EventEnvelope(
            schema_version=SCHEMA_VERSION,
            source_repo="auto-assist",
            event_type="task.candidate.created",
            correlation_id="not-a-uuid",
        )


def test_envelope_rejects_missing_correlation_id_field() -> None:
    """W-06: omitting correlation_id entirely must fail validation at the boundary."""
    with pytest.raises(Exception):
        EventEnvelope(
            schema_version=SCHEMA_VERSION,
            source_repo="auto-assist",
            event_type="task.candidate.created",
        )


def test_envelope_event_link_shape() -> None:
    """W-05: EventLink carries rel/target_type/target_id for FOR_* relations."""
    link = EventLink(rel="FOR_TASK", target_type="Task", target_id="t-1")
    assert link.rel == "FOR_TASK"
    assert link.target_type == "Task"
    assert link.target_id == "t-1"


def test_envelope_accepts_valid_uuid_correlation_id() -> None:
    env = EventEnvelope(
        schema_version=SCHEMA_VERSION,
        source_repo="auto-assist",
        event_type="task.candidate.created",
        correlation_id=_corr(),
    )
    assert env.correlation_id
    assert env.payload == {}


def test_auth_state_enum_values() -> None:
    assert {a.value for a in AuthState} == {
        "authenticated_scott",
        "unknown_speaker",
        "registered_user_unverified",
        "admin_voice_override",
        "rejected",
    }


def test_envelope_actor_and_links() -> None:
    env = EventEnvelope(
        schema_version=SCHEMA_VERSION,
        source_repo="auto-assist",
        event_type="task.candidate.created",
        correlation_id=_corr(),
        actor=Actor(user_id="scott", auth_state=AuthState.AUTHENTICATED_SCOTT),
        links=[EventLink(rel="FOR_TASK", target_type="Task", target_id="t1")],
    )
    assert env.actor.auth_state == AuthState.AUTHENTICATED_SCOTT
    assert env.links[0].target_type == "Task"


def test_trace_event_and_group_require_correlation_id() -> None:
    with pytest.raises(Exception):
        TraceEvent(correlation_id="bad", event_type="x")
    te = TraceEvent(correlation_id=_corr(), event_type="dispatch.created")
    assert te.id.startswith("te_")
    tg = TraceGroup(correlation_id=_corr(), current_state="RUNNING")
    assert tg.current_state == "RUNNING"
