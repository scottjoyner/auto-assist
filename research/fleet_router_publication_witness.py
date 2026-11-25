"""Unwired research acknowledgment of a local outbox observation.

Caller provides a detached SQLite connection containing the router outbox
schema. No connection is opened here, and no outbox insert/update/delete
is performed. Never use with a live outbox. A local matching row is not
a delivery receipt, physical execution receipt, or authorization.
"""
import hashlib
import json

from fleet_router_observation_journal import (
    _transaction, _allocation_digest, JournalHold, MAX_PAYLOAD_BYTES,
)
from fleet_router_request_observation import observation_key

EVENT_TYPE = "research.request_observation"
SOURCE = "auto-router"


def initialize_acknowledgments(journal_path):
    """Explicit create-only extension; no implicit migration of live state."""
    with _transaction(journal_path) as conn:
        conn.execute("""
            CREATE TABLE observation_publication_witness (
                observation_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                payload_sha256 TEXT NOT NULL
            )
        """)


def acknowledge(journal_path, allocation_key, detached_outbox):
    """Recompute exact local-row evidence and retain the first witness.

    Hold on missing/changed/pruned rows, journal conflict, or changed event
    identity; never republish automatically. The returned status concerns
    a detached view only. Caller must ensure snapshot provenance.
    """
    with _transaction(journal_path) as conn:
        row = conn.execute(
            "SELECT observation_id,payload_sha256,payload_json,conflict "
            "FROM observation_journal WHERE allocation_digest=?",
            (_allocation_digest(allocation_key),)).fetchone()
        if row is None or row[1] is None:
            return "HOLD_NO_RECORDED_OBSERVATION"
        oid, digest, raw, conflict = row
        if conflict:
            return "HOLD_JOURNAL_CONFLICT"
        if (type(raw) is not str or len(raw) > MAX_PAYLOAD_BYTES
                or hashlib.sha256(raw.encode("ascii")).hexdigest() != digest):
            return "HOLD_JOURNAL_PAYLOAD"
        prior = conn.execute(
            "SELECT event_id,payload_sha256 FROM observation_publication_witness "
            "WHERE observation_id=?", (oid,)).fetchone()
        key = observation_key({"observation_id":oid})
        rows = detached_outbox.execute(
            "SELECT event_id,event_type,source_service,length(payload_json),"
            "CASE WHEN length(payload_json)<=? THEN payload_json ELSE NULL END "
            "FROM event_outbox WHERE idempotency_key=? LIMIT 2",
            (MAX_PAYLOAD_BYTES * 2, key)).fetchall()
        if len(rows) != 1:
            return "HOLD_PRIOR_WITNESS_MISSING" if prior else "HOLD_OUTBOX_ROW_UNAVAILABLE"
        event_id, event_type, source, size, payload = rows[0]
        if (type(event_id) is not str or not event_id or len(event_id)>512
                or event_type != EVENT_TYPE or source != SOURCE):
            return "HOLD_OUTBOX_IDENTITY"
        if type(payload) is not str or size > MAX_PAYLOAD_BYTES * 2:
            return "HOLD_OUTBOX_PAYLOAD"
        try:
            # Actual EventOutbox serializes with spaces. Re-encode to compare
            # canonical bytes, rejecting duplicate keys and nonfinite values.
            def unique(pairs):
                obj = {}
                for k,v in pairs:
                    if k in obj:
                        raise ValueError("duplicate")
                    obj[k] = v
                return obj
            canon = json.dumps(json.loads(payload, object_pairs_hook=unique),
                               sort_keys=True,separators=(",",":"),
                               ensure_ascii=True,allow_nan=False)
        except (ValueError,TypeError,RecursionError,OverflowError):
            return "HOLD_OUTBOX_PAYLOAD"
        if canon != raw:
            return "HOLD_OUTBOX_PAYLOAD"
        if prior is not None:
            return "MATCH_REPLAY" if tuple(prior) == (event_id,digest) else "HOLD_WITNESS_CHANGED"
        other = conn.execute(
            "SELECT observation_id FROM observation_publication_witness WHERE event_id=?",
            (event_id,)).fetchone()
        if other:
            return "HOLD_EVENT_ID_REUSED"
        conn.execute("INSERT INTO observation_publication_witness VALUES (?,?,?)",
                     (oid,event_id,digest))
        return "LOCAL_OUTBOX_MATCH_RECORDED"
