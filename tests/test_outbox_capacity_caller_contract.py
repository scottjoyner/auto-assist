"""Capacity backpressure is explicit to all three known outbox callers.

No real HTTP, services, credentials, or production outboxes are accessed.
"""

from types import SimpleNamespace

import pytest
import requests

from assistx import auto_assign_client, auto_ingest_client
from assistx.agents.hermes_memory_provider import HermesMemoryProvider
from assistx.outbox_client import OutboxCapacityExceeded, OutboxClient


class FullQueue:
    def enqueue(self, event):
        raise OutboxCapacityExceeded("outbox pending capacity reached")


def test_task_notification_returns_false_on_capacity_without_crashing(monkeypatch):
    monkeypatch.setattr(
        auto_assign_client,
        "settings",
        SimpleNamespace(auto_assign_base_url="https://synthetic.invalid"),
    )
    monkeypatch.setattr(auto_assign_client, "_outbox", FullQueue())
    assert auto_assign_client.notify_task_created("synthetic-capacity-task") is False


def test_ingest_notification_returns_false_on_capacity(monkeypatch):
    monkeypatch.setattr(auto_ingest_client, "_outbox", FullQueue())
    assert (
        auto_ingest_client.notify_context_available("synthetic-capacity-context")
        is False
    )


def test_hermes_capacity_is_explicit_error_not_silent_loss(monkeypatch, tmp_path):
    queue = OutboxClient(
        db_path=str(tmp_path / "synthetic-hermes.sqlite"),
        api_url="",
        auto_flush=False,
    )
    monkeypatch.setattr(queue, "enqueue", FullQueue().enqueue)
    provider = HermesMemoryProvider(
        base_url="http://localhost:8000",
        outbox=queue,
    )

    def unavailable(*_args, **_kwargs):
        raise requests.ConnectionError("synthetic service unavailable")

    monkeypatch.setattr(provider, "_post", unavailable)
    with pytest.raises(OutboxCapacityExceeded, match="pending capacity"):
        provider.signal_event(
            "synthetic-id",
            "synthetic-signal",
            {"test_only": True},
        )
