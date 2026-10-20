"""Task creation must report whether Auto-Assign event enqueue succeeded.

Offline synthetic source test; never constructs a real Neo4j driver or socket.
"""

import logging

import pytest

from assistx import neo4j_client as graph


@pytest.mark.parametrize("queued", [True, False])
def test_task_creation_exposes_notification_enqueue_disposition(
    monkeypatch, caplog, queued
):
    client = object.__new__(graph.Neo4jClient)

    def fake_upsert(**kwargs):
        return "synthetic-task-id"

    monkeypatch.setattr(client, "upsert_ticket", fake_upsert)
    calls = []

    def fake_notify(**kwargs):
        calls.append(kwargs["task_id"])
        return queued

    monkeypatch.setattr(graph, "notify_task_created", fake_notify)
    with caplog.at_level(logging.WARNING):
        result = client.create_task_with_context(
            title="synthetic task",
            auto_dispatch=False,
        )

    assert result["task_id"] == "synthetic-task-id"
    assert result["auto_assign_notification_queued"] is queued
    assert result["context_packet_id"] is None
    assert calls == ["synthetic-task-id"]
    if queued:
        assert "explicit reconciliation" not in caplog.text
    else:
        assert "explicit reconciliation is required" in caplog.text


def test_nonboolean_enqueue_receipt_does_not_claim_persistence(monkeypatch):
    client = object.__new__(graph.Neo4jClient)
    monkeypatch.setattr(
        client, "upsert_ticket", lambda **kwargs: "synthetic-task-id"
    )
    monkeypatch.setattr(graph, "notify_task_created", lambda **kwargs: None)
    outcome = client.create_task_with_context(
        title="synthetic task",
        auto_dispatch=False,
    )
    assert outcome["auto_assign_notification_queued"] is False
