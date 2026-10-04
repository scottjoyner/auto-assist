from __future__ import annotations

from unittest.mock import patch

from assistx import auto_ingest_client as client


def test_auto_ingest_event_matches_assistx_boundary_contract() -> None:
    with patch.object(client._outbox, "enqueue") as enqueue:
        assert client.notify_evidence_linked(
            node_id="dashcam-123",
            evidence_ref="evidence-456",
            enrichment_kind="location",
            correlation_id="12345678-1234-4234-8234-123456789abc",
        ) is True

    body = enqueue.call_args.args[0]
    assert body["event_type"] == "ingest.evidence.linked"
    assert body["source_repo"] == "auto-assist"
    assert body["node_id"] == "dashcam-123"
    assert body["schema_version"] == "2026-08.v1"
    assert body["idempotency_key"].endswith(":12345678-1234-4234-8234-123456789abc")
    assert body["subject"] == {"kind": "event", "id": body["event_id"]}
    assert isinstance(body["links"], list)
    assert {link["target_id"] for link in body["links"]} == {"dashcam-123", "evidence-456"}
    assert body["privacy"]["pii"] is False


def test_auto_ingest_context_event_has_typed_link_and_required_fields() -> None:
    with patch.object(client._outbox, "enqueue") as enqueue:
        assert client.notify_context_available(
            context_packet_id="context-789",
            summary="ready",
            correlation_id="12345678-1234-4234-8234-123456789abc",
        ) is True

    body = enqueue.call_args.args[0]
    assert body["event_type"] == "context.available"
    assert body["links"] == [
        {"rel": "REFERENCES", "target_type": "ContextPacket", "target_id": "context-789"}
    ]
    assert body["occurred_at"].endswith("+00:00")


def test_auto_ingest_does_not_use_auto_assign_endpoint_in_source() -> None:
    source = client.__file__
    text = open(source, encoding="utf-8").read()
    assert "settings.auto_assign_base_url" not in text
    assert 'os.getenv("ASSISTX_API_URL", "")' in text
