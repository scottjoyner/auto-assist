"""Append-only local prepare/ack ledger for encrypted NAS publications.

This is crash-consistency evidence on the controller, NOT an external WORM
witness and not a credential/authorization grant. Never delete or reset it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any

from .trace_execution_adapter import TraceDenied
from .trace_segment_bundle import _private, _read
from .trace_segment_plan import canonical

INTENT_SCHEMA = "assistx.trace-nas-intent.v1"
INTENT_NAME = "publication-intents.jsonl"
_PHASES = frozenset({"prepared", "acknowledged"})


def intent_metadata(
    *,
    node_id: str,
    journal_sha256: str,
    ciphertext_index_sha256: str,
    mount_source: str,
    mount_target: str,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "journal_sha256": journal_sha256,
        "ciphertext_index_sha256": ciphertext_index_sha256,
        "mount_source": mount_source,
        "mount_target": mount_target,
    }


def read_intents(root: Path, signing_key: bytes) -> list[dict[str, Any]]:
    _private(root)
    path = root / INTENT_NAME
    if not path.exists():
        return []
    raw = _read(path, 8 * 1024 * 1024)
    if raw and not raw.endswith(b"\n"):
        raise TraceDenied("nas_intent_torn")
    records: list[dict[str, Any]] = []
    previous = ""
    states: dict[tuple[str, str], str] = {}
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError) as exc:
            raise TraceDenied("nas_intent_invalid") from exc
        if not isinstance(event, dict):
            raise TraceDenied("nas_intent_invalid")
        signature = event.get("signature")
        unsigned = {key: value for key, value in event.items() if key != "signature"}
        phase = unsigned.get("phase")
        identity = (unsigned.get("node_id"), unsigned.get("journal_sha256"))
        if (
            phase not in _PHASES
            or unsigned.get("schema") != INTENT_SCHEMA
            or unsigned.get("previous_signature") != previous
            or not isinstance(signature, str)
            or not hmac.compare_digest(
                signature, hmac.new(signing_key, canonical(unsigned), hashlib.sha256).hexdigest()
            )
        ):
            raise TraceDenied("nas_intent_invalid")
        state = states.get(identity)
        if (phase == "prepared" and state is not None) or (phase == "acknowledged" and state != "prepared"):
            raise TraceDenied("nas_intent_phase_invalid")
        states[identity] = phase
        previous = signature
        records.append(event)
    return records


def intent_state(
    events: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> tuple[bool, bool]:
    matches = []
    for event in events:
        if event.get("node_id") != metadata["node_id"] or event.get("journal_sha256") != metadata["journal_sha256"]:
            continue
        if any(event.get(k) != value for k, value in metadata.items()):
            raise TraceDenied("nas_intent_metadata_conflict")
        matches.append(event["phase"])
    if matches not in ([], ["prepared"], ["prepared", "acknowledged"]):
        raise TraceDenied("nas_intent_phase_invalid")
    return bool(matches), matches == ["prepared", "acknowledged"]


def append_intent(
    root: Path,
    *,
    signing_key: bytes,
    metadata: dict[str, Any],
    phase: str,
) -> None:
    """Caller holds the exclusive publisher lock, then fsyncs the append."""
    if phase not in _PHASES:
        raise TraceDenied("nas_intent_phase_invalid")
    events = read_intents(root, signing_key)
    prepared, acknowledged = intent_state(events, metadata)
    if (phase == "prepared" and prepared) or (phase == "acknowledged" and (not prepared or acknowledged)):
        raise TraceDenied("nas_intent_phase_invalid")
    unsigned = {
        "schema": INTENT_SCHEMA,
        "phase": phase,
        **metadata,
        "previous_signature": events[-1]["signature"] if events else "",
    }
    row = dict(unsigned)
    row["signature"] = hmac.new(signing_key, canonical(unsigned), hashlib.sha256).hexdigest()
    path = root / INTENT_NAME
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid() or st.st_nlink != 1:
            raise TraceDenied("nas_intent_file_unsafe")
        encoded = canonical(row) + b"\n"
        if os.write(fd, encoded) != len(encoded):
            raise TraceDenied("nas_intent_short_write")
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
