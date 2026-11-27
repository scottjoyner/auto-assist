"""Round-20: the durable operation journal must fail closed on corrupt state.

``AppendOnlyOperationJournal`` is local durability for finalization operations
that cannot yet reach the graph.  ``_read_locked`` is the single admission
point for every reader (``entries``, ``pending``, ``verify``, ``replay``) and
for ``append`` itself, which reads the tail before extending the hash chain.

The existing suite pins only one corruption edge (a payload tamper that breaks
the checksum).  These tests pin the *other* truncated/corrupt durable-state
edges and, crucially, that they fail closed on **every** reader and on the
write path -- not just ``verify()``.  All state is a private ``tmp_path``
JSONL file; no graph, service, network, credential or live DB is touched.
"""
from __future__ import annotations

import json

import pytest

from assistx.operational_journal import (
    AppendOnlyOperationJournal,
    JournalCorruption,
    _entry_hash,
)


def _values(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _rewrite(path, values):
    path.write_text("".join(json.dumps(value) + "\n" for value in values))


def _rechecksum(value):
    """Return a copy whose checksum matches its (possibly tampered) body."""
    body = {key: item for key, item in value.items() if key != "checksum"}
    return {**body, "checksum": _entry_hash(body)}


def _journal(path):
    return AppendOnlyOperationJournal(path, clock_ms=lambda: 1_000)


def test_truncated_partial_json_line_fails_closed_on_every_reader(tmp_path):
    path = tmp_path / "operations.jsonl"
    journal = _journal(path)
    journal.append(idempotency_key="task:1", status="PENDING", payload={"n": 1})
    # Simulate a crash mid-write: a partial JSON line with no closing brace.
    with path.open("a") as handle:
        handle.write('{"sequence": 2, "entry_id": "partial"')

    with pytest.raises(JournalCorruption, match="not valid JSON"):
        journal.entries()
    with pytest.raises(JournalCorruption, match="not valid JSON"):
        journal.verify()
    # replay() consumes pending() -> entries(), so it must refuse too.
    with pytest.raises(JournalCorruption, match="not valid JSON"):
        journal.replay(lambda _envelope: "ok")


def test_non_object_json_line_fails_closed(tmp_path):
    path = tmp_path / "operations.jsonl"
    journal = _journal(path)
    for raw in ('["an", "array"]\n', '"a scalar"\n', "42\n"):
        path.write_text(raw)
        with pytest.raises(JournalCorruption, match="not an object"):
            journal.entries()


def test_broken_hash_chain_fails_closed_even_with_a_valid_checksum(tmp_path):
    path = tmp_path / "operations.jsonl"
    journal = _journal(path)
    journal.append(idempotency_key="task:1", status="PENDING", payload={"n": 1})
    journal.append(idempotency_key="task:2", status="PENDING", payload={"n": 2})
    values = _values(path)
    # Tamper the chain link but repair the checksum, so only the chain check
    # can catch this (proves that check has teeth independently of checksum).
    values[1]["previous_hash"] = "f" * 64
    _rewrite(path, [_rechecksum(value) for value in values])

    with pytest.raises(JournalCorruption, match="hash chain mismatch"):
        journal.verify()


def test_sequence_gap_fails_closed_even_with_a_valid_checksum(tmp_path):
    path = tmp_path / "operations.jsonl"
    journal = _journal(path)
    journal.append(idempotency_key="task:1", status="PENDING", payload={"n": 1})
    values = _values(path)
    values[0]["sequence"] = 7
    _rewrite(path, [_rechecksum(value) for value in values])

    with pytest.raises(JournalCorruption, match="sequence mismatch"):
        journal.entries()


def test_corruption_in_the_middle_is_detected_not_only_at_the_tail(tmp_path):
    path = tmp_path / "operations.jsonl"
    journal = _journal(path)
    for index in (1, 2, 3):
        journal.append(
            idempotency_key=f"task:{index}", status="PENDING", payload={"n": index}
        )
    values = _values(path)
    values[1]["payload"]["n"] = 99  # payload tamper, checksum left stale
    _rewrite(path, values)

    with pytest.raises(JournalCorruption, match="checksum mismatch"):
        journal.verify()


def test_append_refuses_to_extend_a_corrupt_journal(tmp_path):
    path = tmp_path / "operations.jsonl"
    journal = _journal(path)
    journal.append(idempotency_key="task:1", status="PENDING", payload={"n": 1})
    with path.open("a") as handle:
        handle.write('{"sequence":')

    # append() reads the tail first; it must abort rather than append a new
    # record onto an unreadable chain.
    with pytest.raises(JournalCorruption, match="not valid JSON"):
        journal.append(idempotency_key="task:2", status="PENDING", payload={"n": 2})
    # Exactly the original complete line plus the partial fragment remain: no
    # new durable record was written.
    assert path.read_text().count("\n") == 1


def test_valid_journal_is_not_rejected_by_the_strict_reader(tmp_path):
    path = tmp_path / "operations.jsonl"
    journal = _journal(path)
    first = journal.append(idempotency_key="task:1", status="PENDING", payload={"n": 1})
    second = journal.append(idempotency_key="task:2", status="PENDING", payload={"n": 2})

    report = journal.verify()
    assert report["ok"] is True
    assert report["entries"] == 2
    assert report["last_sequence"] == 2
    assert report["last_checksum"] == second.checksum
    assert report["pending"] == 2
    assert [entry.entry_id for entry in journal.entries()] == [
        first.entry_id,
        second.entry_id,
    ]
