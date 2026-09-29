"""Versioned, vendor-neutral AssistX trace envelope helpers."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

TRACE_SCHEMA_VERSION = "assistx.trace.v1"
ALLOWED_SPAN_TYPES = {
    "task",
    "plan",
    "route",
    "model",
    "tool",
    "test",
    "memory_read",
    "memory_write",
    "retry",
    "admission",
    "rollback",
    "outcome",
}
SENSITIVE_ATTRIBUTE_KEYS = {
    "authorization",
    "api_key",
    "token",
    "password",
    "secret",
    "prompt_raw",
    "response_raw",
}


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def validate_trace(trace: dict[str, Any]) -> None:
    """Validate the minimal AssistX normalized trace contract."""
    if trace.get("schema_version") != TRACE_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {trace.get('schema_version')!r}")
    _require_nonempty_string(trace.get("trace_id"), "trace_id")

    experiment = trace.get("experiment")
    if experiment is not None:
        if not isinstance(experiment, dict):
            raise ValueError("experiment must be an object")
        _require_nonempty_string(experiment.get("name"), "experiment.name")
        _require_nonempty_string(experiment.get("variant"), "experiment.variant")
        baseline_id = experiment.get("baseline_id")
        if baseline_id is not None:
            _require_nonempty_string(baseline_id, "experiment.baseline_id")
        source_commit = experiment.get("source_commit")
        if source_commit is not None:
            _require_nonempty_string(source_commit, "experiment.source_commit")
        upstream = experiment.get("upstream")
        if upstream is not None and not isinstance(upstream, dict):
            raise ValueError("experiment.upstream must be an object")

    spans = trace.get("spans")
    if not isinstance(spans, list):
        raise ValueError("spans must be a list")
    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            raise ValueError(f"spans[{index}] must be an object")
        span_type = _require_nonempty_string(span.get("type"), f"spans[{index}].type")
        if span_type not in ALLOWED_SPAN_TYPES:
            raise ValueError(f"unsupported span type: {span_type}")
        attributes = span.get("attributes", {})
        if not isinstance(attributes, dict):
            raise ValueError(f"spans[{index}].attributes must be an object")

    outcome = trace.get("outcome")
    if not isinstance(outcome, dict):
        raise ValueError("outcome must be an object")
    if outcome.get("status") not in {"success", "failure", "cancelled", "timed_out"}:
        raise ValueError("outcome.status is invalid")
    evidence_ids = outcome.get("evidence_ids", [])
    if not isinstance(evidence_ids, list) or not all(isinstance(v, str) for v in evidence_ids):
        raise ValueError("outcome.evidence_ids must be a list of strings")


def redact_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized copy suitable for local fixtures/telemetry sinks."""
    sanitized = deepcopy(trace)
    for span in sanitized.get("spans", []):
        if not isinstance(span, dict):
            continue
        attributes = span.get("attributes")
        if not isinstance(attributes, dict):
            continue
        for key in list(attributes):
            if key.lower() in SENSITIVE_ATTRIBUTE_KEYS:
                attributes[key] = "[REDACTED]"
    return sanitized


def canonical_json(trace: dict[str, Any], *, redact: bool = True) -> str:
    """Validate and serialize a stable JSON representation for fixtures/adapters."""
    validate_trace(trace)
    payload = redact_trace(trace) if redact else deepcopy(trace)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
