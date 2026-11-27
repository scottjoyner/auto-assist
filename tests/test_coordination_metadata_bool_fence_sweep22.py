"""Hermetic regression tests for strict boolean parsing in coordination metadata.

Synthetic in-process dicts only: no socket, HTTP, Redis, Neo4j, SQLite, DB/WAL,
docker, credential file, recovery-hold file or network mount is read. These
tests pin that producer-supplied safety flags in the derived request/task
metadata are decoded from canonical boolean spellings and that malformed
claims fall back to the RESTRICTIVE value instead of Python's ``bool()``
truthiness (where ``bool("false") is True``).
"""

from __future__ import annotations

import pytest

from assistx.coordination_metadata import (
    build_event_metadata,
    build_task_candidate_metadata,
)


def _task(**overrides):
    task = {
        "task_id": "task-1",
        "title": "probe",
        "prompt": "probe",
        "privacy": "internal",
        "local_only": True,
        "allow_cloud": False,
        "sensitive": True,
        "max_completion_tokens": 700,
    }
    task.update(overrides)
    return task


def _event(**overrides):
    event = {
        "event_id": "evt-1",
        "idempotency_key": "idem-1",
        "event_type": "task.candidate.created",
        "source_repo": "auto-assist",
        "privacy": {"pii": True, "privacy_class": "private", "retention_class": "keep"},
    }
    event.update(overrides)
    return event


def _task_bools(task):
    metadata = build_task_candidate_metadata(task)
    return {
        "request": (
            metadata["request"]["local_only"],
            metadata["request"]["allow_cloud"],
            metadata["request"]["sensitive"],
        ),
        "task": (
            metadata["task"]["local_only"],
            metadata["task"]["allow_cloud"],
            metadata["task"]["sensitive"],
        ),
    }


# --- fail-open: an explicit cloud denial is upgraded to an approval ---------


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "FALSE", " false ", 0])
def test_explicit_allow_cloud_denial_is_not_upgraded(raw):
    bools = _task_bools(_task(allow_cloud=raw))
    assert bools["request"][1] is False
    assert bools["task"][1] is False


@pytest.mark.parametrize("raw", ["", "   ", "garbage", [], {}, object()])
def test_malformed_allow_cloud_falls_back_to_deny(raw):
    bools = _task_bools(_task(allow_cloud=raw))
    assert bools["request"][1] is False
    assert bools["task"][1] is False


@pytest.mark.parametrize("raw", ["", "garbage", 0, None])
def test_malformed_local_only_falls_back_to_restrictive(raw):
    bools = _task_bools(_task(local_only=raw))
    assert bools["request"][0] is True
    assert bools["task"][0] is True


@pytest.mark.parametrize("raw", ["", "garbage", 0, None])
def test_malformed_sensitive_falls_back_to_restrictive(raw):
    bools = _task_bools(_task(sensitive=raw))
    assert bools["request"][2] is True
    assert bools["task"][2] is True


# --- canonical spellings decode identically in both metadata sections ------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
@pytest.mark.parametrize("field", ["local_only", "allow_cloud", "sensitive"])
def test_canonical_spellings_decode_in_both_sections(field, raw, expected):
    index = {"local_only": 0, "allow_cloud": 1, "sensitive": 2}[field]
    bools = _task_bools(_task(**{field: raw}))
    assert bools["request"][index] is expected
    assert bools["task"][index] is expected


# --- event privacy pii flag -------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_event_pii_canonical_spellings_decode(raw, expected):
    metadata = build_event_metadata(_event(privacy={"pii": raw}))
    assert metadata["request"]["pii"] is expected


@pytest.mark.parametrize("raw", ["", "garbage", 0, None])
def test_event_pii_malformed_falls_back_to_restrictive(raw):
    metadata = build_event_metadata(_event(privacy={"pii": raw}))
    assert metadata["request"]["pii"] is True


# --- controls: behavior that must stay unchanged ----------------------------


def test_explicit_boolean_flags_are_unchanged():
    bools = _task_bools(_task(local_only=False, allow_cloud=True, sensitive=False))
    assert bools["request"] == (False, True, False)
    assert bools["task"] == (False, True, False)


def test_event_privacy_class_and_retention_are_unchanged():
    metadata = build_event_metadata(
        _event(privacy={"pii": "false", "privacy_class": "secret", "retention_class": "keep"})
    )
    assert metadata["request"]["privacy_class"] == "secret"
    assert metadata["request"]["retention_class"] == "keep"


def test_max_completion_tokens_unchanged():
    metadata = build_task_candidate_metadata(_task(max_completion_tokens=1234))
    assert metadata["request"]["max_completion_tokens"] == 1234
    assert metadata["task"]["max_completion_tokens"] == 1234
