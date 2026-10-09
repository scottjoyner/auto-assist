"""Additive Ed25519 producer envelope for legacy encrypted trace bundles.

The producer alone reads the legacy HMAC signing secret. A receiver verifies
the detached envelope with a pinned PUBLIC key and independently restores the
encrypted bytes without receiving producer HMAC authority.

Research-only: no service, key provisioning, NAS writes or WORM guarantees.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .trace_execution_adapter import TraceDenied, TraceReceiptStore
from .trace_segment_bundle import _check_passphrase, _filename, _gpg, _private, _read, verify_bundle
from .trace_segment_plan import canonical, digest

SCHEMA = "assistx.trace-producer-manifest.v2"
LEGACY_SCHEMA = "assistx.encrypted-trace-segments.v1"
GENESIS = "0" * 64
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_.:@-]{1,120}\Z")
_SIG = re.compile(r"[A-Za-z0-9_-]{86}\Z")
_FIELDS = frozenset(
    {
        "schema",
        "algorithm",
        "producer_id",
        "key_id",
        "node_id",
        "journal_sha256",
        "index_sha256",
        "records",
        "last_hash",
        "generation",
        "previous_manifest_sha256",
        "issued_at_ms",
        "segments",
    }
)
_SEG_FIELDS = frozenset({"file", "ciphertext_sha256", "plaintext_sha256"})


def _require_id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise TraceDenied("producer_manifest_identity_invalid")
    return value


def _require_hex(value: Any) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise TraceDenied("producer_manifest_digest_invalid")
    return value


def _require_positive(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise TraceDenied("producer_manifest_integer_invalid")
    return value


def _index(root: Path) -> tuple[bytes, dict[str, Any], dict[str, Any], list[Any]]:
    raw = _read(root / "index.json", 8 * 1024 * 1024)
    try:
        index = json.loads(raw)
        if not isinstance(index, dict) or set(index) != {"schema", "plan", "entries", "signature"}:
            raise ValueError()
        if index["schema"] != LEGACY_SCHEMA:
            raise ValueError()
        plan, entries = index["plan"], index["entries"]
        if not isinstance(plan, dict) or not isinstance(entries, list) or not isinstance(plan.get("segments"), list):
            raise ValueError()
        if not entries or len(entries) != len(plan["segments"]) or len(entries) > 8192:
            raise ValueError()
        if not isinstance(index["signature"], str) or len(index["signature"]) != 64:
            raise ValueError()
    except (ValueError, TypeError, KeyError) as exc:
        raise TraceDenied("producer_legacy_index_invalid") from exc
    return raw, index, plan, entries


def _segments_from_index(plan: dict[str, Any], entries: list[Any]) -> list[dict[str, str]]:
    result = []
    for i, (meta, entry) in enumerate(zip(plan["segments"], entries)):  # noqa: B905 -- Python 3.9
        if not isinstance(meta, dict) or not isinstance(entry, dict):
            raise TraceDenied("producer_index_segment_invalid")
        plaintext_hash = _require_hex(meta.get("sha256"))
        ciphertext_hash = _require_hex(entry.get("ciphertext_sha256"))
        name = _filename(i + 1, plaintext_hash)
        if entry.get("file") != name:
            raise TraceDenied("producer_index_filename_mismatch")
        result.append(
            {
                "file": name,
                "ciphertext_sha256": ciphertext_hash,
                "plaintext_sha256": plaintext_hash,
            }
        )
    return result


def manifest_digest(manifest: dict[str, Any]) -> str:
    if not isinstance(manifest, dict) or set(manifest) != _FIELDS | {"signature"}:
        raise TraceDenied("producer_manifest_schema_invalid")
    return digest(canonical(manifest))


def sign_verified_legacy_bundle(
    *,
    archive: Path,
    node_id: str,
    signing_key: bytes,
    passphrase: Path,
    producer_signer: Ed25519PrivateKey,
    producer_id: str,
    key_id: str,
    generation: int,
    previous_manifest_sha256: str,
    issued_at_ms: int,
) -> dict[str, Any]:
    """Producer-only trusted upgrade, no changes to original archive files."""
    _require_id(node_id)
    _require_id(producer_id)
    _require_id(key_id)
    _require_positive(generation)
    _require_positive(issued_at_ms)
    _require_hex(previous_manifest_sha256)
    if generation == 1 and previous_manifest_sha256 != GENESIS:
        raise TraceDenied("producer_manifest_genesis_invalid")
    if not isinstance(producer_signer, Ed25519PrivateKey):
        raise TraceDenied("producer_signer_invalid")
    if archive.is_symlink() or archive.parent.is_symlink():
        raise TraceDenied("producer_archive_symlink")
    _private(archive)
    raw = verify_bundle(archive, node_id=node_id, passphrase=passphrase, signing_key=signing_key)
    if archive.parent.name != node_id or archive.name != digest(raw):
        raise TraceDenied("producer_archive_namespace_mismatch")
    index_bytes, _, plan, entries = _index(archive)
    if (
        plan.get("node_id") != node_id
        or plan.get("journal_sha256") != digest(raw)
        or plan.get("records") != len(TraceReceiptStore._verify_data(raw))
    ):
        raise TraceDenied("producer_journal_index_mismatch")
    records = TraceReceiptStore._verify_data(raw)
    if plan.get("last_hash") != records[-1]["entry_hash"]:
        raise TraceDenied("producer_journal_head_mismatch")
    segments = _segments_from_index(plan, entries)
    for entry in segments:
        if digest(_read(archive / entry["file"], 8 * 1024 * 1024 + 100_000)) != entry["ciphertext_sha256"]:
            raise TraceDenied("producer_ciphertext_mismatch")
    unsigned = {
        "schema": SCHEMA,
        "algorithm": "Ed25519",
        "producer_id": producer_id,
        "key_id": key_id,
        "node_id": node_id,
        "journal_sha256": digest(raw),
        "index_sha256": digest(index_bytes),
        "records": len(records),
        "last_hash": records[-1]["entry_hash"],
        "generation": generation,
        "previous_manifest_sha256": previous_manifest_sha256,
        "issued_at_ms": issued_at_ms,
        "segments": segments,
    }
    signature = base64.urlsafe_b64encode(producer_signer.sign(canonical(unsigned))).decode("ascii").rstrip("=")
    return {**unsigned, "signature": signature}


def verify_and_restore_public(
    *,
    archive: Path,
    manifest: dict[str, Any],
    producer_verifier: Ed25519PublicKey,
    passphrase: Path,
    producer_id: str,
    key_id: str,
    node_id: str,
    expected_journal_sha256: str,
    expected_index_sha256: str,
    expected_generation: int,
    expected_previous_manifest_sha256: str,
    minimum_issued_at_ms: int,
) -> bytes:
    """Receiver verifies Ed25519 and decrypts WITHOUT producer HMAC secret.

    The external caller must pin producer key and independent expected state.
    No return value or data supplied here means a production custody ACK.
    """
    if not isinstance(manifest, dict) or set(manifest) != _FIELDS | {"signature"}:
        raise TraceDenied("producer_manifest_schema_invalid")
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    if unsigned["schema"] != SCHEMA or unsigned["algorithm"] != "Ed25519":
        raise TraceDenied("producer_manifest_schema_invalid")
    for value in (unsigned["producer_id"], unsigned["key_id"], unsigned["node_id"]):
        _require_id(value)
    for value in (
        unsigned["journal_sha256"],
        unsigned["index_sha256"],
        unsigned["last_hash"],
        unsigned["previous_manifest_sha256"],
    ):
        _require_hex(value)
    for value in (unsigned["records"], unsigned["generation"], unsigned["issued_at_ms"]):
        _require_positive(value)
    if type(minimum_issued_at_ms) is not int or minimum_issued_at_ms < 0:
        raise TraceDenied("producer_minimum_timestamp_invalid")
    if (
        unsigned["producer_id"] != producer_id
        or unsigned["key_id"] != key_id
        or unsigned["node_id"] != node_id
        or unsigned["journal_sha256"] != expected_journal_sha256
        or unsigned["index_sha256"] != expected_index_sha256
    ):
        raise TraceDenied("producer_manifest_binding_mismatch")
    if (
        unsigned["generation"] != expected_generation
        or unsigned["previous_manifest_sha256"] != expected_previous_manifest_sha256
        or unsigned["issued_at_ms"] < minimum_issued_at_ms
    ):
        raise TraceDenied("producer_manifest_replay_or_rollback")
    if expected_generation == 1 and expected_previous_manifest_sha256 != GENESIS:
        raise TraceDenied("producer_manifest_genesis_invalid")
    entries = unsigned["segments"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 8192:
        raise TraceDenied("producer_manifest_segments_invalid")
    if any(
        not isinstance(entry, dict)
        or set(entry) != _SEG_FIELDS
        or not isinstance(entry["file"], str)
        or any(
            not isinstance(entry[name], str) or not _HEX.fullmatch(entry[name])
            for name in ("ciphertext_sha256", "plaintext_sha256")
        )
        for entry in entries
    ):
        raise TraceDenied("producer_manifest_segment_schema_invalid")
    if not isinstance(producer_verifier, Ed25519PublicKey):
        raise TraceDenied("producer_verifier_invalid")
    signature = manifest["signature"]
    if not isinstance(signature, str) or not _SIG.fullmatch(signature):
        raise TraceDenied("producer_manifest_signature_invalid")
    try:
        producer_verifier.verify(base64.urlsafe_b64decode(signature + "=="), canonical(unsigned))
    except (InvalidSignature, ValueError) as exc:
        raise TraceDenied("producer_manifest_signature_invalid") from exc
    if archive.is_symlink() or archive.parent.is_symlink():
        raise TraceDenied("producer_archive_symlink")
    _private(archive)
    if archive.name != expected_journal_sha256 or archive.parent.name != node_id:
        raise TraceDenied("producer_archive_namespace_mismatch")
    _check_passphrase(passphrase)
    index_bytes, _, plan, legacy_entries = _index(archive)
    if digest(index_bytes) != expected_index_sha256:
        raise TraceDenied("producer_legacy_index_changed")
    if (
        plan.get("node_id") != node_id
        or plan.get("records") != unsigned["records"]
        or plan.get("last_hash") != unsigned["last_hash"]
        or plan.get("journal_sha256") != expected_journal_sha256
        or len(legacy_entries) != len(entries)
    ):
        raise TraceDenied("producer_legacy_plan_changed")
    expected_segments = _segments_from_index(plan, legacy_entries)
    if any(not isinstance(entry, dict) or set(entry) != _SEG_FIELDS for entry in entries):
        raise TraceDenied("producer_manifest_segment_schema_invalid")
    if entries != expected_segments:
        raise TraceDenied("producer_manifest_index_entries_mismatch")
    pieces = []
    for entry in entries:
        ciphertext_path = archive / entry["file"]
        ciphertext = _read(ciphertext_path, 8 * 1024 * 1024 + 100_000)
        if digest(ciphertext) != entry["ciphertext_sha256"]:
            raise TraceDenied("producer_ciphertext_mismatch")
        plaintext = _gpg(None, ciphertext_path, passphrase, decrypt=True)
        if digest(plaintext) != entry["plaintext_sha256"]:
            raise TraceDenied("producer_plaintext_mismatch")
        if not plaintext.endswith(b"\n"):
            raise TraceDenied("producer_segment_row_boundary_invalid")
        pieces.append(plaintext)
    raw = b"".join(pieces)
    if digest(raw) != expected_journal_sha256:
        raise TraceDenied("producer_restored_journal_mismatch")
    records = TraceReceiptStore._verify_data(raw)
    if (
        len(records) != unsigned["records"]
        or not records
        or records[-1]["entry_hash"] != unsigned["last_hash"]
        or any(row.get("node_id") != node_id for row in records)
    ):
        raise TraceDenied("producer_restored_chain_mismatch")
    return raw
