"""Fail-closed regression: canonical trace envelopes must reject the nil UUID.

The ``correlation_id`` validators treat ``uuid.UUID(v)`` parsing success as
sufficient.  The all-zeros (nil) UUID parses successfully but identifies no
trace, so an event carrying it is accepted and collapses into a single shared
TraceGroup.  These tests pin the boundary: a nil correlation must be rejected
for every canonical envelope type and in every ``uuid.UUID``-equivalent
textual representation, while real UUIDs keep working.

Hermetic: only pydantic model validation plus the in-memory FakeNeo4j are
exercised.  No Neo4j container, HTTP endpoint, SQLite DB, credential file,
recovery-hold file, docker daemon or network mount is touched.
"""

from __future__ import annotations

import uuid

import pytest

from assistx.contracts.event_envelope import EventEnvelope, TraceEvent, TraceGroup
from assistx.contracts.tests.fake_neo4j import FakeNeo4j
from assistx.contracts.version import SCHEMA_VERSION
from assistx.swarm_core import record_trace_from_envelope


# Every spelling that ``uuid.UUID()`` accepts for the all-zeros value.
NIL_FORMS = [
    "00000000-0000-0000-0000-000000000000",  # canonical dashed
    "0" * 32,  # bare 32-hex
    "{00000000-0000-0000-0000-000000000000}",  # braced
    "urn:uuid:00000000-0000-0000-0000-000000000000",  # URN
]


def _envelope(corr: str) -> EventEnvelope:
    return EventEnvelope(
        schema_version=SCHEMA_VERSION,
        source_repo="auto-assist",
        event_type="task.candidate.created",
        correlation_id=corr,
    )


def _trace_event(corr: str) -> TraceEvent:
    return TraceEvent(correlation_id=corr, event_type="task.candidate.created")


def _trace_group(corr: str) -> TraceGroup:
    return TraceGroup(correlation_id=corr)


BUILDERS = [
    ("EventEnvelope", _envelope),
    ("TraceEvent", _trace_event),
    ("TraceGroup", _trace_group),
]


@pytest.mark.parametrize("model,builder", BUILDERS)
def test_nil_uuid_is_rejected(model: str, builder) -> None:
    """A nil correlation is not a trace identifier: fail closed."""
    with pytest.raises(Exception, match="nil"):
        builder(NIL_FORMS[0])


@pytest.mark.parametrize("model,builder", BUILDERS)
@pytest.mark.parametrize("nil_form", NIL_FORMS)
def test_every_nil_representation_is_rejected(model: str, builder, nil_form: str) -> None:
    """Alternate spellings of all-zeros must not slip past the validator."""
    with pytest.raises(Exception):
        builder(nil_form)


@pytest.mark.parametrize("model,builder", BUILDERS)
def test_real_uuid_is_still_accepted(model: str, builder) -> None:
    """Guard against over-rejection: a real UUID must pass unchanged."""
    corr = uuid.uuid4().hex
    built = builder(corr)
    assert built.correlation_id == corr


def test_nil_correlation_never_creates_a_trace_group() -> None:
    """Downstream: a nil correlation must not become a TraceGroup key.

    On the defective base the envelope is accepted, the trace is recorded and
    the assertion below fails (a TraceGroup keyed by the nil UUID exists).
    After the fix, construction itself raises the nil-UUID validation error.
    """
    with pytest.raises(Exception, match="nil"):
        env = _envelope(NIL_FORMS[0])
        neo = FakeNeo4j(apoc=False)
        record_trace_from_envelope(neo, env, source="auto-assist")
        assert not neo.has_node("TraceGroup", "correlation_id", NIL_FORMS[0])
