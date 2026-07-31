from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import fcntl


class JournalCorruption(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _entry_hash(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(body))).hexdigest()


@dataclass(frozen=True)
class JournalEntry:
    sequence: int
    entry_id: str
    idempotency_key: str
    status: str
    payload: dict[str, Any]
    created_at_ms: int
    previous_hash: str
    checksum: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "entry_id": self.entry_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "payload": self.payload,
            "created_at_ms": self.created_at_ms,
            "previous_hash": self.previous_hash,
            "checksum": self.checksum,
        }


class AppendOnlyOperationJournal:
    """Process-safe, hash-chained JSONL journal for Neo4j replay.

    The journal is local durability for operations that cannot yet reach Neo4j.
    It is not final authority. A key remains pending until a COMMITTED record is
    appended after the idempotent Neo4j sink succeeds.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    @contextmanager
    def _locked(self) -> Iterator[BinaryIO]:
        handle = self.path.open("a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def append(
        self,
        *,
        idempotency_key: str,
        status: str,
        payload: Mapping[str, Any],
    ) -> JournalEntry:
        key = str(idempotency_key or "").strip()
        normalized_status = str(status or "").strip().upper()
        if not key or len(key) > 300:
            raise ValueError("a bounded idempotency_key is required")
        if normalized_status not in {"PENDING", "RETRY", "COMMITTED", "ABORTED"}:
            raise ValueError(f"unsupported journal status: {normalized_status}")
        normalized_payload = dict(payload)

        with self._locked() as handle:
            entries = self._read_locked(handle)
            previous = entries[-1].checksum if entries else "0" * 64
            sequence = entries[-1].sequence + 1 if entries else 1
            created_at_ms = self.clock_ms()
            identity = {
                "idempotency_key": key,
                "status": normalized_status,
                "payload": normalized_payload,
            }
            entry_id = "journal-" + hashlib.sha256(_canonical(identity)).hexdigest()
            # Repeating the exact transition is a no-op. This prevents retries
            # after an uncertain HTTP response from growing duplicate records.
            existing = next(
                (
                    entry
                    for entry in reversed(entries)
                    if entry.entry_id == entry_id
                ),
                None,
            )
            if existing is not None:
                return existing
            unsigned = {
                "sequence": sequence,
                "entry_id": entry_id,
                "idempotency_key": key,
                "status": normalized_status,
                "payload": normalized_payload,
                "created_at_ms": created_at_ms,
                "previous_hash": previous,
            }
            checksum = _entry_hash(unsigned)
            entry = JournalEntry(checksum=checksum, **unsigned)
            handle.seek(0, os.SEEK_END)
            handle.write(_canonical(entry.as_dict()) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
            return entry

    def entries(self) -> list[JournalEntry]:
        with self._locked() as handle:
            return self._read_locked(handle)

    def pending(self) -> list[JournalEntry]:
        latest: dict[str, JournalEntry] = {}
        committed: set[str] = set()
        for entry in self.entries():
            latest[entry.idempotency_key] = entry
            if entry.status in {"COMMITTED", "ABORTED"}:
                committed.add(entry.idempotency_key)
        return [
            entry
            for key, entry in latest.items()
            if key not in committed and entry.status in {"PENDING", "RETRY"}
        ]

    def replay(
        self,
        commit: Callable[[dict[str, Any]], Any],
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        attempted = 0
        committed = 0
        failures: list[dict[str, str]] = []
        for entry in self.pending()[: max(1, min(int(limit), 10_000))]:
            attempted += 1
            envelope = {
                **entry.payload,
                "journal_entry_id": entry.entry_id,
                "journal_checksum": entry.checksum,
                "idempotency_key": entry.idempotency_key,
                "durable_commit_id": "durable-"
                + hashlib.sha256(entry.idempotency_key.encode()).hexdigest(),
            }
            try:
                result = commit(envelope)
            except Exception as exc:
                failures.append(
                    {
                        "idempotency_key": entry.idempotency_key,
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
                self.append(
                    idempotency_key=entry.idempotency_key,
                    status="RETRY",
                    payload=entry.payload,
                )
                continue
            self.append(
                idempotency_key=entry.idempotency_key,
                status="COMMITTED",
                payload={
                    **entry.payload,
                    "durable_result": result,
                    "source_entry_id": entry.entry_id,
                },
            )
            committed += 1
        return {
            "attempted": attempted,
            "committed": committed,
            "failed": len(failures),
            "failures": failures,
            "remaining": len(self.pending()),
        }

    def verify(self) -> dict[str, Any]:
        entries = self.entries()
        return {
            "ok": True,
            "entries": len(entries),
            "last_sequence": entries[-1].sequence if entries else 0,
            "last_checksum": entries[-1].checksum if entries else "0" * 64,
            "pending": len(self.pending()),
        }

    @staticmethod
    def _read_locked(handle: BinaryIO) -> list[JournalEntry]:
        handle.seek(0)
        entries: list[JournalEntry] = []
        expected_previous = "0" * 64
        for line_number, raw in enumerate(handle.readlines(), start=1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise JournalCorruption(
                    f"journal line {line_number} is not valid JSON"
                ) from exc
            if not isinstance(value, dict):
                raise JournalCorruption(
                    f"journal line {line_number} is not an object"
                )
            supplied_checksum = str(value.pop("checksum", ""))
            calculated = _entry_hash(value)
            if supplied_checksum != calculated:
                raise JournalCorruption(
                    f"journal line {line_number} checksum mismatch"
                )
            if str(value.get("previous_hash") or "") != expected_previous:
                raise JournalCorruption(
                    f"journal line {line_number} hash chain mismatch"
                )
            sequence = int(value.get("sequence") or 0)
            if sequence != len(entries) + 1:
                raise JournalCorruption(
                    f"journal line {line_number} sequence mismatch"
                )
            entry = JournalEntry(
                sequence=sequence,
                entry_id=str(value.get("entry_id") or ""),
                idempotency_key=str(value.get("idempotency_key") or ""),
                status=str(value.get("status") or ""),
                payload=dict(value.get("payload") or {}),
                created_at_ms=int(value.get("created_at_ms") or 0),
                previous_hash=str(value.get("previous_hash") or ""),
                checksum=supplied_checksum,
            )
            entries.append(entry)
            expected_previous = supplied_checksum
        return entries
