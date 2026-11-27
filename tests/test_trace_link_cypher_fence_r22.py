"""Round 22: canonical trace links must not become Cypher injection vectors.

``record_trace_event`` interpolates each link's ``rel`` (relationship type) and
``target_type`` (node label) into a Cypher string. Those values arrive from the
canonical ``EventEnvelope.links`` at the HTTP boundary, so they are untrusted.
This module pins that both the envelope model and the recorder fence them to
simple identifiers.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from assistx.contracts.event_envelope import EventEnvelope, EventLink
from assistx.contracts.tests.fake_neo4j import FakeNeo4j
from assistx.swarm_core import record_trace_event, record_trace_from_envelope


def _envelope(links):
    return EventEnvelope(
        schema_version="2026-06-08.v1",
        source_repo="auto-assist",
        event_type="task.candidate.created",
        correlation_id=str(uuid.uuid4()),
        links=links,
    )


def _cypher(neo: FakeNeo4j) -> str:
    return "\n".join(run["cypher"] for run in neo.runs)


def test_event_link_rejects_injection_in_rel() -> None:
    """A relationship type carrying Cypher syntax is rejected at the boundary."""
    with pytest.raises(ValidationError):
        EventLink(
            rel="FOR_TASK]->(x) DETACH DELETE x //",
            target_type="Task",
            target_id="t1",
        )


def test_event_link_rejects_injection_in_target_type() -> None:
    """A node label carrying Cypher syntax is rejected at the boundary."""
    with pytest.raises(ValidationError):
        EventLink(
            rel="FOR_TASK",
            target_type="Task) DETACH DELETE n //",
            target_id="t1",
        )


def test_envelope_with_injected_link_is_rejected() -> None:
    """The canonical envelope cannot smuggle an injected link past validation."""
    with pytest.raises(ValidationError):
        _envelope([{"rel": "FOR_TASK", "target_type": "Task)//", "target_id": "t1"}])


def test_recorder_never_emits_injected_link_cypher() -> None:
    """Direct record_trace_event callers cannot inject via structured links."""
    neo = FakeNeo4j()
    record_trace_event(
        neo,
        correlation_id=str(uuid.uuid4()),
        event_type="task.candidate.created",
        source="auto-assist",
        links=[
            {
                "rel": "FOR_TASK]->(x) DETACH DELETE x //",
                "target_type": "Task",
                "target_id": "t1",
            },
            {
                "rel": "FOR_TASK",
                "target_type": "Task) DETACH DELETE n //",
                "target_id": "t2",
            },
        ],
    )
    assert "DETACH DELETE" not in _cypher(neo)


def test_safe_link_still_records_relationship() -> None:
    """Positive control: a well-formed link is still wired into the graph."""
    neo = FakeNeo4j()
    record_trace_from_envelope(
        neo,
        _envelope([EventLink(rel="FOR_TASK", target_type="Task", target_id="task-7")]),
    )
    assert neo.has_relationship("FOR_TASK", to_label="Task", to_id="task-7")
