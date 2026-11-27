from __future__ import annotations

from unittest.mock import patch
from uuid import UUID

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
    assert body["schema_version"] == "2026-06-08.v1"
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


def test_evidence_linked_refuses_empty_endpoints_without_enqueueing() -> None:
    """An empty endpoint would yield a 'linked' event with no links at all."""
    with patch.object(client._outbox, "enqueue") as enqueue:
        assert client.notify_evidence_linked(node_id="", evidence_ref="evidence-1") is False
        assert client.notify_evidence_linked(node_id="node-1", evidence_ref="  ") is False
        enqueue.assert_not_called()


def test_context_available_refuses_empty_packet_id_without_enqueueing() -> None:
    """The packet id is the event's only target; empty means nothing to consume."""
    with patch.object(client._outbox, "enqueue") as enqueue:
        assert client.notify_context_available(context_packet_id="") is False
        enqueue.assert_not_called()


def test_padded_identifiers_are_normalized_in_emitted_links_and_node_id() -> None:
    """Guards strip to validate, so emission must strip too — no padded IDs."""
    with patch.object(client._outbox, "enqueue") as enqueue:
        assert client.notify_evidence_linked(
            node_id="  dashcam-123  ",
            evidence_ref="\t evidence-456 \n",
        ) is True

    body = enqueue.call_args.args[0]
    assert body["node_id"] == "dashcam-123"
    assert {link["target_id"] for link in body["links"]} == {"dashcam-123", "evidence-456"}
    assert body["payload"]["node_id"] == "dashcam-123"
    assert body["payload"]["evidence_ref"] == "evidence-456"


def test_padded_context_packet_id_is_normalized_in_emitted_link() -> None:
    with patch.object(client._outbox, "enqueue") as enqueue:
        assert client.notify_context_available(context_packet_id="  context-789\t") is True

    body = enqueue.call_args.args[0]
    assert body["links"] == [
        {"rel": "REFERENCES", "target_type": "ContextPacket", "target_id": "context-789"}
    ]
    assert body["payload"]["context_packet_id"] == "context-789"


def test_blank_correlation_id_is_replaced_with_a_contract_uuid() -> None:
    """Whitespace-only ids are truthy, so the old `or` fallback let them through."""
    with patch.object(client._outbox, "enqueue") as enqueue:
        assert client.notify_evidence_linked(
            node_id="dashcam-123", evidence_ref="evidence-456", correlation_id="   "
        ) is True

    body = enqueue.call_args.args[0]
    assert str(UUID(body["correlation_id"])) == body["correlation_id"]
    assert body["idempotency_key"] == f"ingest.evidence.linked:{body['correlation_id']}"


def test_malformed_correlation_id_is_replaced_for_context_event() -> None:
    """A non-UUID correlation is a 422 at the sink, not a retriable event."""
    with patch.object(client._outbox, "enqueue") as enqueue:
        assert client.notify_context_available(
            context_packet_id="context-789", correlation_id="not-a-uuid"
        ) is True

    body = enqueue.call_args.args[0]
    assert UUID(body["correlation_id"])
    assert body["correlation_id"] != "not-a-uuid"
    assert body["idempotency_key"].endswith(f":{body['correlation_id']}")


def test_emitted_events_pass_sink_envelope_validation() -> None:
    """The producer's schema_version must be one the sink will actually persist.

    ``record_event`` (POST /api/events) calls ``validate_event_envelope``,
    which rejects any schema_version outside SUPPORTED_SCHEMA_VERSIONS with a
    400. An unlisted version can only retry into the outbox dead-letter state.
    """
    from assistx.swarm_core import SUPPORTED_SCHEMA_VERSIONS, validate_event_envelope

    emitters = (
        lambda: client.notify_evidence_linked(node_id="n-1", evidence_ref="e-1"),
        lambda: client.notify_context_available(context_packet_id="c-1"),
    )
    for emit in emitters:
        with patch.object(client._outbox, "enqueue") as enqueue:
            assert emit() is True
        body = enqueue.call_args.args[0]
        assert body["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
        validate_event_envelope(body)  # must not raise EventValidationError
