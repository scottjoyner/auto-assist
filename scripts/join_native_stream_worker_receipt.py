"""Join native LM Studio SSE with signed gateway+worker TEST receipts, offline.

Mathematical join only. No issuer trust or physical worker execution attested.
This accepts only a sanitized/caller-authorized SSE and externally provided
already-captured signed envelopes. It never invokes a model or reads files.
"""
from __future__ import annotations

import re
from typing import Any

from verify_lmstudio_native_stream_instance import StreamEvidence
from verify_lmstudio_worker_request_pair import correlate

OPAQUE_ID = re.compile(r"[a-zA-Z0-9_-]{16,96}\Z")


def join(
    *, stream: StreamEvidence, gateway_envelope: Any,
    worker_envelope: Any, trusted_gateway_public_key: bytes,
    trusted_worker_public_key: bytes, expected_request_id: str,
    now_ms: int, already_seen_request_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Reject valid-looking but inconsistent SSE and paired signed receipts."""
    if not isinstance(stream, StreamEvidence):
        raise ValueError("native stream evidence must be parser validated")
    if not isinstance(expected_request_id, str) or not OPAQUE_ID.fullmatch(
        expected_request_id
    ):
        raise ValueError("expected authenticated ingress request ID malformed")
    pair = correlate(
        gateway_envelope=gateway_envelope,
        worker_envelope=worker_envelope,
        trusted_gateway_public_key=trusted_gateway_public_key,
        trusted_worker_public_key=trusted_worker_public_key,
        now_ms=now_ms, already_seen_request_ids=already_seen_request_ids,
    )
    if pair.request_id != expected_request_id:
        raise ValueError("signed request IDs do not bind expected ingress request")
    if pair.model_instance_id != stream.model_instance_id:
        raise ValueError("signed model instance differs from native stream selection")
    if pair.response_id != stream.response_id:
        raise ValueError("signed gateway response ID differs from native stream")
    if pair.output_sha256 != stream.output_sha256:
        raise ValueError("signed worker output digest differs from native stream")
    return {
        "schema": "lmstudio-native-stream-pair-review-v1",
        "status": "NATIVE_STREAM_AND_TEST_RECEIPTS_CONSISTENT_ONLY",
        "model_instance_id": stream.model_instance_id,
        "request_id": pair.request_id,
        "response_id": pair.response_id,
        "worker_pid_from_untrusted_receipt": pair.worker_pid,
        "worker_start_ticks_from_untrusted_receipt": pair.worker_start_ticks,
        "output_sha256": stream.output_sha256,
        "signed_worker_receipt_sha256": pair.receipt_sha256,
        "producer_public_keys_independently_trusted": False,
        "physical_worker_process_execution_attested": False,
        "ingress_owner_binding_attested": False,
        "live_model_or_signer_contacted": False,
        "operator_approval_present": False,
        "can_renew": False,
        "can_dispatch": False,
    }
