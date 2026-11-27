"""Fail-closed coercion of string safety flags on task candidates (round 34).

Task payloads arrive as JSON and may carry boolean flags as strings from legacy
producers. ``bool("false")`` is ``True`` in Python, so a naive coercion upgrades
an explicit cloud opt-out into a cloud opt-in. These tests are hermetic: they
only exercise the pure ``_normalize_task_candidate`` function with in-memory
dictionaries -- no DB, network, model, service or mount access.
"""

from __future__ import annotations

import json

import pytest

from assistx.router_integration import _normalize_task_candidate


def _candidate(**payload: object) -> dict[str, object]:
    task = {
        "id": "task-r34",
        "title": "Flag coercion probe",
        "status": "READY",
        "payload_json": json.dumps(payload),
    }
    return _normalize_task_candidate(task)


@pytest.mark.parametrize("spelling", ["false", "FALSE", " false ", "0", "no", "off"])
def test_explicit_false_allow_cloud_stays_denied(spelling: str) -> None:
    candidate = _candidate(allow_cloud=spelling)

    assert candidate["allow_cloud"] is False
    # Enriched metadata is consumed downstream and must agree with the flag.
    assert candidate["metadata"]["request"]["allow_cloud"] is False
    assert candidate["metadata"]["task"]["allow_cloud"] is False


def test_explicit_true_allow_cloud_stays_allowed() -> None:
    assert _candidate(allow_cloud="true")["allow_cloud"] is True
    assert _candidate(allow_cloud="yes")["allow_cloud"] is True


@pytest.mark.parametrize("value", ["maybe", [], {}, 17])
def test_unrecognized_allow_cloud_value_fails_closed(value: object) -> None:
    assert _candidate(allow_cloud=value)["allow_cloud"] is False


def test_false_allow_cloud_from_metadata_json_is_also_denied() -> None:
    task = {
        "id": "task-r34-meta",
        "title": "Metadata flag probe",
        "status": "READY",
        "metadata_json": json.dumps({"allow_cloud": "false"}),
    }

    assert _normalize_task_candidate(task)["allow_cloud"] is False


@pytest.mark.parametrize("spelling", ["false", "FALSE", "0", "no", "off"])
def test_explicit_false_local_only_and_sensitive_stay_false(spelling: str) -> None:
    candidate = _candidate(local_only=spelling, sensitive=spelling)

    assert candidate["local_only"] is False
    assert candidate["sensitive"] is False


@pytest.mark.parametrize("value", ["maybe", [], {}])
def test_unrecognized_restrictive_flags_stay_restrictive(value: object) -> None:
    """Unknown values must not weaken privacy-derived restrictions."""
    candidate = _candidate(local_only=value, sensitive=value)

    assert candidate["local_only"] is True
    assert candidate["sensitive"] is True
    assert candidate["allow_cloud"] is False


def test_private_task_with_unrecognized_boolean_string_stays_local() -> None:
    task = {
        "id": "task-r34-private",
        "title": "Private task",
        "status": "READY",
        "privacy": "private",
        "payload_json": json.dumps({"local_only": "maybe", "allow_cloud": "true"}),
    }

    candidate = _normalize_task_candidate(task)

    assert candidate["local_only"] is True
    assert candidate["sensitive"] is True
    assert candidate["allow_cloud"] is False


def test_absent_flags_keep_legacy_defaults() -> None:
    candidate = _candidate(description="No flags supplied")

    assert candidate["local_only"] is False
    assert candidate["sensitive"] is False
    assert candidate["allow_cloud"] is True
