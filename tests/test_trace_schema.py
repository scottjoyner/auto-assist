import json
from pathlib import Path

import pytest

from assistx.evaluation.trace_schema import (
    TRACE_SCHEMA_VERSION,
    canonical_json,
    redact_trace,
    validate_trace,
)

FIXTURE = Path(__file__).parent / "fixtures" / "evaluation" / "good_trace_v1.json"


def _fixture():
    return json.loads(FIXTURE.read_text())


def test_fixture_matches_current_schema_and_serializes_stably():
    trace = _fixture()
    validate_trace(trace)
    payload = canonical_json(trace)
    assert json.loads(payload) == trace
    assert trace["schema_version"] == TRACE_SCHEMA_VERSION


def test_sensitive_attributes_are_redacted_without_mutating_source():
    trace = _fixture()
    trace["spans"][0]["attributes"]["api_key"] = "super-secret"
    sanitized = redact_trace(trace)
    assert sanitized["spans"][0]["attributes"]["api_key"] == "[REDACTED]"
    assert trace["spans"][0]["attributes"]["api_key"] == "super-secret"


def test_unknown_schema_version_is_rejected():
    trace = _fixture()
    trace["schema_version"] = "assistx.trace.v999"
    with pytest.raises(ValueError, match="unsupported schema_version"):
        validate_trace(trace)


def test_unknown_span_type_is_rejected():
    trace = _fixture()
    trace["spans"].append({"type": "mystery", "attributes": {}})
    with pytest.raises(ValueError, match="unsupported span type"):
        validate_trace(trace)


def test_invalid_outcome_is_rejected():
    trace = _fixture()
    trace["outcome"]["status"] = "maybe"
    with pytest.raises(ValueError, match="outcome.status"):
        validate_trace(trace)
