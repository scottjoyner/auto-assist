"""W-06: trace linkage contract tests.

These run WITHOUT a live Neo4j by driving ``swarm_core.record_trace_*``
against an in-memory fake store that records the Cypher statements it is
asked to run. They assert the three hard requirements from LLD §2 / W-05:

  (a) an inbound envelope lacking a valid ``correlation_id`` is rejected;
  (b) an envelope that links to a ``Task`` creates
      ``(:TraceEvent)-[:FOR_TASK]->(:Task)``;
  (c) the envelope's ``TraceGroup.current_state`` is upserted from the
      latest ``event_type``.
"""

from __future__ import annotations

import uuid

import pytest

from .fake_neo4j import FakeNeo4j, CapturingSession

from ..event_envelope import EventEnvelope, EventLink, Actor, AuthState
from ...swarm_core import (
    record_trace_event,
    record_trace_from_envelope,
)


def _valid_uuid() -> str:
    return uuid.uuid4().hex


def test_inbound_envelope_without_correlation_id_rejected() -> None:
    """(a) A canonical envelope must carry a valid (UUID) correlation_id."""
    with pytest.raises(Exception):
        EventEnvelope(
            schema_version="2026-06-08.v1",
            source_repo="auto-assist",
            event_type="task.candidate.created",
            # correlation_id deliberately omitted
        )

    # A non-UUID string must also be rejected at the boundary.
    with pytest.raises(Exception):
        EventEnvelope(
            schema_version="2026-06-08.v1",
            source_repo="auto-assist",
            event_type="task.candidate.created",
            correlation_id="not-a-uuid",
        )


def test_trace_event_creates_for_task_link() -> None:
    """(b) A Task link produces (:TraceEvent)-[:FOR_TASK]->(:Task)."""
    neo = FakeNeo4j(apoc=False)
    corr = _valid_uuid()
    task_id = "task-abc-123"

    record_trace_event(
        neo,
        correlation_id=corr,
        event_type="assignment.claimed",
        source="auto-assist",
        task_id=task_id,
    )

    # The TraceEvent node must exist.
    assert neo.has_node("TraceEvent", "event_id")

    # The FOR_TASK relationship back to the Task must have been created.
    assert neo.has_relationship("FOR_TASK", from_label="TraceEvent", to_label="Task", to_id=task_id)

    # The correlation group is linked.
    assert neo.has_node("TraceGroup", "correlation_id", corr)


def test_trace_event_links_dispatch_assignment_route() -> None:
    """(b) dispatch/assignment/route ids build the matching FOR_* links."""
    neo = FakeNeo4j()
    corr = _valid_uuid()
    record_trace_event(
        neo,
        correlation_id=corr,
        event_type="router.route_decision",
        source="auto-router",
        dispatch_id="d-1",
        route_id="r-1",
        assignment_id="a-1",
    )
    assert neo.has_relationship("FOR_DISPATCH", to_label="Dispatch", to_id="d-1")
    assert neo.has_relationship("FOR_ROUTE", to_label="RouteDecision", to_id="r-1")
    assert neo.has_relationship("FOR_ASSIGNMENT", to_label="Assignment", to_id="a-1")


def test_trace_group_current_state_upserted_from_event_type() -> None:
    """(c) TraceGroup.current_state reflects the latest event_type."""
    neo = FakeNeo4j(apoc=False)
    corr = _valid_uuid()

    # First observation: a route decision -> pending_assignment.
    record_trace_event(
        neo, correlation_id=corr, event_type="router.route_decision", source="auto-router"
    )
    state = neo.get_property("TraceGroup", "correlation_id", corr, "current_state")
    assert state == "pending_assignment"

    # Latest observation wins: a completion -> completed.
    record_trace_event(
        neo, correlation_id=corr, event_type="assignment.completed", source="auto-router"
    )
    state = neo.get_property("TraceGroup", "correlation_id", corr, "current_state")
    assert state == "completed"


def test_record_trace_from_envelope_links_via_envelope_links() -> None:
    """Envelope ``links`` drive the trace linkage (W-05 contract path)."""
    neo = FakeNeo4j(apoc=False)
    corr = _valid_uuid()
    env = EventEnvelope(
        schema_version="2026-06-08.v1",
        source_repo="auto-assist",
        event_type="task.candidate.created",
        correlation_id=corr,
        actor=Actor(user_id="scott", auth_state=AuthState.AUTHENTICATED_SCOTT),
        links=[EventLink(rel="FOR_TASK", target_type="Task", target_id="task-x")],
    )
    record_trace_from_envelope(neo, env, source="auto-assist")
    assert neo.has_relationship("FOR_TASK", to_label="Task", to_id="task-x")
    assert neo.has_node("TraceGroup", "correlation_id", corr)
    state = neo.get_property("TraceGroup", "correlation_id", corr, "current_state")
    assert state == "pending"


def test_apoc_fallback_path_also_updates_state() -> None:
    """Even without APOC, inline recompute keeps current_state correct."""
    neo = FakeNeo4j(apoc=False)
    corr = _valid_uuid()
    record_trace_event(
        neo, correlation_id=corr, event_type="assignment.failed", source="auto-assist"
    )
    state = neo.get_property("TraceGroup", "correlation_id", corr, "current_state")
    assert state == "failed"
