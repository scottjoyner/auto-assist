"""Synthetic API test: task creation exposes Auto-Assign queue disposition.

No Neo4j connection or event is created; the API driver is a fake.
"""

import pytest

from assistx import api


@pytest.mark.parametrize("queued", [True, False])
def test_task_api_returns_notification_queue_disposition(monkeypatch, queued):
    observed = []

    class FakeGraph:
        def create_task_with_context(self, **kwargs):
            observed.append(kwargs["title"])
            return {
                "task_id": "synthetic-api-task",
                "dispatch_id": None,
                "auto_assign_notification_queued": queued,
            }

        def close(self):
            observed.append("closed")

    monkeypatch.setattr(api, "_neo", FakeGraph)
    result = api.api_create_task(
        api.TaskCreateIn(title="synthetic created task"),
        user="synthetic",
    )
    assert result["task_id"] == "synthetic-api-task"
    assert result["auto_assign_notification_queued"] is queued
    assert observed == ["synthetic created task", "closed"]


def test_task_api_missing_queuing_attestation_is_false(monkeypatch):
    class FakeGraph:
        def create_task_with_context(self, **kwargs):
            return {"task_id": "synthetic-task", "dispatch_id": None}

        def close(self):
            pass

    monkeypatch.setattr(api, "_neo", FakeGraph)
    result = api.api_create_task(
        api.TaskCreateIn(title="synthetic no receipt"), user="synthetic"
    )
    assert result["auto_assign_notification_queued"] is False
