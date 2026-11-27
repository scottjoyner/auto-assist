"""Freshly derived coordination metadata must outrank stale producer data (r15).

Pure in-memory dict tests: no Neo4j, Redis, HTTP, filesystem or credential
access. Both production builders are imported directly; nothing is installed
or started.
"""
from __future__ import annotations

import pytest

from assistx.coordination_metadata import (
    build_event_metadata,
    build_event_request_metadata,
    build_task_candidate_metadata,
    build_task_candidate_request_metadata,
)


def _task(**overrides):
    task = {
        "task_id": "t-1",
        "title": "authoritative title",
        "prompt": "authoritative prompt",
        "local_only": True,
        "allow_cloud": False,
        "sensitive": True,
        "privacy": "secret",
        "metadata": {
            "request": {
                "allow_cloud": True,
                "local_only": False,
                "sensitive": False,
                "event_id": "stale-event-id",
                "privacy_class": "public",
            },
            "task": {"title": "stale title", "prompt": "stale prompt"},
            "custom_producer_key": "kept",
            # Pin the clock-derived field so direct-builder comparisons are exact.
            "occurred_at": "2026-01-01T00:00:00+00:00",
        },
    }
    task.update(overrides)
    return task


def test_stale_allow_cloud_cannot_override_derived_denial():
    task = _task()
    out = build_task_candidate_metadata(task, task["metadata"])
    derived = build_task_candidate_request_metadata(task)
    # The top-level flag is authoritative; the producer's nested copy is stale.
    assert task["allow_cloud"] is False
    assert out["request"]["allow_cloud"] is False
    assert derived["allow_cloud"] is False


def test_stale_local_only_and_sensitive_cannot_override_derived_flags():
    task = _task()
    out = build_task_candidate_metadata(task, task["metadata"])
    assert out["request"]["local_only"] is True
    assert out["request"]["sensitive"] is True


def test_stale_request_id_and_privacy_class_cannot_override_derived_values():
    task = _task()
    out = build_task_candidate_metadata(task, task["metadata"])
    # request_id/source_event_id/privacy are derived from the authoritative
    # task; event_id is not a derived key, so a producer-supplied value simply
    # survives as an extra field and is not a stale override.
    assert out["request"]["request_id"] == "t-1"
    assert out["request"]["source_event_id"] == ""
    assert out["request"]["privacy"] == "secret"


def test_unknown_custom_existing_keys_are_still_preserved():
    task = _task()
    out = build_task_candidate_metadata(task, task["metadata"])
    assert out["custom_producer_key"] == "kept"


def test_task_section_derived_title_and_prompt_win():
    task = _task()
    out = build_task_candidate_metadata(task, task["metadata"])
    assert out["task"]["title"] == "authoritative title"
    assert out["task"]["prompt"] == "authoritative prompt"


def test_event_request_stale_id_and_type_cannot_override_derived_values():
    event = {
        "event_id": "real-1",
        "idempotency_key": "idem-1",
        "event_type": "real-type",
        "source_repo": "real-repo",
        "metadata": {
            "request": {
                "event_id": "stale-1",
                "event_type": "fake-type",
                "source_repo": "fake-repo",
            }
        },
    }
    out = build_event_metadata(event, event["metadata"])
    derived = build_event_request_metadata(event)
    assert out["request"]["event_id"] == "real-1"
    assert out["request"]["event_type"] == "real-type"
    assert out["request"]["source_repo"] == "real-repo"
    assert out["request"]["idempotency_key"] == derived["idempotency_key"]


def test_no_existing_metadata_matches_direct_builder():
    task = _task(metadata={"occurred_at": "2026-01-01T00:00:00+00:00"})
    out = build_task_candidate_metadata(task, None)
    assert out["request"] == build_task_candidate_request_metadata(task)


def test_non_mapping_existing_metadata_is_ignored():
    task = _task(metadata="not-a-mapping")
    out = build_task_candidate_metadata(task, task["metadata"])
    assert out["request"]["allow_cloud"] is False
    assert out["request"]["local_only"] is True
    assert out["request"]["sensitive"] is True
