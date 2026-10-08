"""Read-only immutable segment planning for large append-only trace journals.

Does not upload, encrypt, delete or truncate anything. Caller must later
encrypt each segment and independently witness a durable signed manifest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .trace_execution_adapter import TraceDenied, TraceReceiptStore

SCHEMA = "assistx.trace-segment-plan.v1"
MAX_SEGMENT_BYTES = 8 * 1024 * 1024
DEFAULT_SEGMENT_BYTES = 1024 * 1024


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _checked_rows(raw: bytes, node_id: str) -> list[dict[str, Any]]:
    if not isinstance(raw, bytes) or not raw:
        raise TraceDenied("segment_source_empty")
    records = TraceReceiptStore._verify_data(raw)
    if not records or any(r.get("node_id") != node_id for r in records):
        raise TraceDenied("segment_node_identity_mismatch")
    return records


def plan_segments(
    raw: bytes,
    *,
    node_id: str,
    signing_key: bytes,
    max_segment_bytes: int = DEFAULT_SEGMENT_BYTES,
) -> tuple[dict[str, Any], list[bytes]]:
    """Precompute exact record-boundary bytes and HMAC-protected segment heads."""
    if type(max_segment_bytes) is not int or not 1024 <= max_segment_bytes <= MAX_SEGMENT_BYTES:
        raise TraceDenied("invalid_segment_size")
    if not isinstance(signing_key, bytes) or len(signing_key) < 32:
        raise TraceDenied("invalid_segment_signing_key")
    rows = _checked_rows(raw, node_id)
    lines = raw.splitlines(keepends=True)
    if len(lines) != len(rows):
        raise TraceDenied("segment_row_mismatch")
    segments: list[bytes] = []
    manifest_rows: list[dict[str, Any]] = []
    prev_segment_digest = "0" * 64
    current = []
    start = 0
    current_bytes = 0

    def emit(end: int) -> None:
        nonlocal current, start, current_bytes, prev_segment_digest
        segment = b"".join(current)
        meta = {
            "segment": len(segments) + 1,
            "first_seq": start + 1,
            "last_seq": end,
            "previous_row_hash": rows[start]["prev_hash"],
            "last_row_hash": rows[end - 1]["entry_hash"],
            "previous_segment_digest": prev_segment_digest,
            "bytes": len(segment),
            "sha256": digest(segment),
        }
        prev_segment_digest = digest(canonical(meta))
        segments.append(segment)
        manifest_rows.append(meta)
        current = []
        current_bytes = 0
        start = end

    for i, line in enumerate(lines):
        if len(line) > max_segment_bytes:
            raise TraceDenied("trace_record_exceeds_segment_size")
        if current and current_bytes + len(line) > max_segment_bytes:
            emit(i)
        current.append(line)
        current_bytes += len(line)
    if current:
        emit(len(rows))
    manifest = {
        "schema": SCHEMA,
        "node_id": node_id,
        "records": len(rows),
        "journal_sha256": digest(raw),
        "last_hash": rows[-1]["entry_hash"],
        "bytes": len(raw),
        "segment_limit": max_segment_bytes,
        "segments": manifest_rows,
    }
    manifest["signature"] = hmac.new(signing_key, canonical(manifest), hashlib.sha256).hexdigest()
    return manifest, segments


def verify_segments(
    manifest: dict[str, Any],
    segments: list[bytes],
    *,
    node_id: str,
    signing_key: bytes,
) -> bytes:
    """Return original unmodified journal only after all links and HMAC verify."""
    if not isinstance(manifest, dict) or not isinstance(segments, list):
        raise TraceDenied("invalid_segment_manifest")
    signature = manifest.get("signature")
    if not isinstance(signature, str):
        raise TraceDenied("invalid_segment_signature")
    unsigned = {k: v for k, v in manifest.items() if k != "signature"}
    actual = hmac.new(signing_key, canonical(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, actual):
        raise TraceDenied("segment_signature_mismatch")
    if unsigned.get("schema") != SCHEMA or unsigned.get("node_id") != node_id:
        raise TraceDenied("segment_manifest_identity_mismatch")
    length = unsigned.get("segments")
    if not isinstance(length, list) or not length or len(length) != len(segments):
        raise TraceDenied("segment_count_mismatch")
    prev_row_hash = "0" * 64
    prev_segment_digest = "0" * 64
    prior_seq = 0
    for i, (metadata, chunk) in enumerate(zip(length, segments)):  # noqa: B905 -- Python 3.9 compatibility
        if not isinstance(metadata, dict) or not isinstance(chunk, bytes):
            raise TraceDenied("segment_invalid_entry")
        if (
            metadata.get("segment") != i + 1
            or metadata.get("first_seq") != prior_seq + 1
            or metadata.get("previous_row_hash") != prev_row_hash
            or metadata.get("previous_segment_digest") != prev_segment_digest
            or metadata.get("bytes") != len(chunk)
            or metadata.get("sha256") != digest(chunk)
            or len(chunk) > unsigned.get("segment_limit", 0)
        ):
            raise TraceDenied("segment_chain_or_content_mismatch")
        prev_segment_digest = digest(canonical(metadata))
        prev_row_hash = metadata.get("last_row_hash")
        prior_seq = metadata.get("last_seq")
    raw = b"".join(segments)
    if (
        prior_seq != unsigned.get("records")
        or prev_row_hash != unsigned.get("last_hash")
        or len(raw) != unsigned.get("bytes")
        or digest(raw) != unsigned.get("journal_sha256")
    ):
        raise TraceDenied("segment_reassembled_journal_mismatch")
    rows = _checked_rows(raw, node_id)
    if len(rows) != prior_seq or rows[-1]["entry_hash"] != prev_row_hash:
        raise TraceDenied("segment_reassembled_chain_mismatch")
    return raw
