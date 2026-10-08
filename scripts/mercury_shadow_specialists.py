#!/usr/bin/env python3
"""Offline Mercury specialist contract. No network, subprocess, or model invocation.

This is a shadow evaluator, not a Mercury runtime client or provider lease authority.
Only scripted synthetic worker events are accepted. Disabled by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Protocol
import fcntl
import json
import math
import os
import re
import sqlite3
import stat
import time
import uuid

MOCK_PROVIDER = "mercury-fixture"
MOCK_MODEL = "mercury-fixture/no-generation"
MOCK_GROUP = "synthetic-upstream-only"
ZERO_HASH = "0" * 64
TRIP_STATUSES = frozenset({401, 402, 403, 429, 503})


@dataclass(frozen=True)
class Specialist:
    role: str
    input_cap: int
    output_cap: int
    step_cap: int
    description: str


SPECIALISTS = {
    "repository-reviewer": Specialist("repository-reviewer", 6000, 800, 3, "Read-only code findings"),
    "trace-auditor": Specialist("trace-auditor", 3000, 500, 3, "Trace integrity classification"),
    "documentation-analyst": Specialist("documentation-analyst", 5000, 900, 3, "Read-only source synthesis"),
}


class CustodyError(RuntimeError):
    """A trace cannot be proven complete; no further dispatch is allowed."""


@dataclass(frozen=True)
class Task:
    task_id: str
    role: str
    objective_sha256: str
    model: str = MOCK_MODEL
    provider: str = MOCK_PROVIDER
    group: str = MOCK_GROUP
    node_id: str = "synthetic-node-1"
    attempt_id: str = "attempt-0001"
    authority_epoch: int = 1

    def validate(self) -> Specialist:
        if not isinstance(self.task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{7,79}", self.task_id):
            raise ValueError("invalid_task_id")
        if self.role not in SPECIALISTS:
            raise ValueError("unknown_specialist")
        for identity in (self.node_id, self.attempt_id):
            if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{5,79}", identity):
                raise ValueError("invalid_execution_identity")
        if type(self.authority_epoch) is not int or self.authority_epoch < 1:
            raise ValueError("invalid_authority_epoch")
        if not isinstance(self.objective_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.objective_sha256):
            raise ValueError("invalid_objective_digest")
        if (self.model, self.provider, self.group) != (MOCK_MODEL, MOCK_PROVIDER, MOCK_GROUP):
            raise ValueError("non_fixture_provider_forbidden")
        return SPECIALISTS[self.role]


@dataclass(frozen=True)
class Lease:
    lease_id: str
    group: str
    expires_at: float
    task_id: str
    node_id: str
    model: str
    role: str
    attempt_id: str
    authority_epoch: int
    reserved_input: int
    reserved_output: int


@dataclass(frozen=True)
class Event:
    kind: str
    input_tokens: int = 0
    output_tokens: int = 0
    error_status: int = 0
    model: str = MOCK_MODEL


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    input_tokens: int
    output_tokens: int
    steps: int
    quality_verified: bool = False
    vendor_quota_verified: bool = False


class Admission(Protocol):
    def acquire(self, task: Task, policy: Specialist) -> Lease | None: ...
    def renew(self, lease: Lease, task: Task) -> Lease | None: ...
    def release(self, lease: Lease, task: Task) -> bool: ...
    def trip(self, task: Task, status: int) -> None: ...


class FixtureWorker:
    """Only worker accepted in shadow mode; cannot dispatch a model or tool."""

    def __init__(self, *events: Event):
        self.script = tuple(events) if events else (Event("usage", input_tokens=10, output_tokens=5),
                                                     Event("finish"))
        self.started = 0
        self.cancelled = 0

    def events(self, task: Task) -> Iterable[Event]:
        self.started += 1
        yield from self.script

    def cancel(self) -> None:
        self.cancelled += 1


def _canon(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _check_records(lines: Iterable[str]) -> list[dict]:
    records: list[dict] = []
    prev = ZERO_HASH
    for raw in lines:
        try:
            record = json.loads(raw)
            digest = record.pop("event_hash")
            if (record["schema"] != "mercury-shadow-event/v1"
                    or record["seq"] != len(records) + 1
                    or record["previous_hash"] != prev
                    or sha256(_canon(record).encode()).hexdigest() != digest):
                raise ValueError("invalid_chain")
            record["event_hash"] = digest
            records.append(record)
            prev = digest
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise CustodyError("trace_chain_invalid") from exc
    return records


class Journal:
    """Private local append-only *prototype*; NOT independently witnessed/WORM."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise CustodyError("unsafe_journal_path")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _open(self):
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            os.close(fd)
            raise CustodyError("untrusted_journal")
        fcntl.flock(fd, fcntl.LOCK_EX)
        return os.fdopen(fd, "a+", encoding="utf-8")

    def records(self) -> list[dict]:
        try:
            with self._open() as stream:
                stream.seek(0)
                return _check_records(stream.readlines())
        except OSError as exc:
            raise CustodyError("trace_read_unavailable") from exc

    def append(self, task: Task, kind: str, **fields: object) -> dict:
        # Only these keys are serialized; never prompts, source files, tool output or keys.
        allowed = {"reason", "status", "input_tokens", "output_tokens", "steps",
                   "lease_id", "reserved_input", "reserved_output"}
        if set(fields) - allowed:
            raise CustodyError("unapproved_trace_field")
        try:
            with self._open() as stream:
                stream.seek(0)
                earlier = _check_records(stream.readlines())
                event = {
                    "schema": "mercury-shadow-event/v1",
                    "seq": len(earlier) + 1,
                    "previous_hash": earlier[-1]["event_hash"] if earlier else ZERO_HASH,
                    "task_id": task.task_id,
                    "attempt_id": task.attempt_id,
                    "node_id": task.node_id,
                    "authority_epoch": task.authority_epoch,
                    "role": task.role,
                    "objective_sha256": task.objective_sha256,
                    "provider": task.provider,
                    "model": task.model,
                    "upstream_group": task.group,
                    "observed_at_unix": int(time.time()),
                    "event": kind,
                    **fields,
                }
                event["event_hash"] = sha256(_canon(event).encode()).hexdigest()
                stream.write(_canon(event) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                return event
        except OSError as exc:
            raise CustodyError("trace_write_unavailable") from exc


class MercuryShadowAdapter:
    """An explicitly enabled simulation; no function here calls Mercury or a provider."""

    def __init__(self, journal: Journal, admission: Admission | None = None,
                 *, enabled: bool = False, clock=None, witness=None):
        self.journal = journal
        self.admission = admission
        self.enabled = enabled
        self.clock = clock or time.time
        # This is only an in-process fixture witness; no immutable remote custody is claimed.
        self.witness = witness if witness is not None else MockCustodyWitness()

    def _record(self, task: Task, kind: str, **fields: object) -> None:
        receipt = self.journal.append(task, kind, **fields)
        if not self.witness.acknowledge(receipt):
            raise CustodyError("mock_witness_denied")

    def _valid_lease(self, lease: Lease | None, task: Task) -> bool:
        policy = SPECIALISTS[task.role]
        return (isinstance(lease, Lease)
                and isinstance(lease.lease_id, str)
                and bool(re.fullmatch(r"[A-Za-z0-9_-]{8,128}", lease.lease_id))
                and lease.group == task.group
                and (lease.task_id, lease.node_id, lease.model, lease.role,
                     lease.attempt_id, lease.authority_epoch) ==
                    (task.task_id, task.node_id, task.model, task.role,
                     task.attempt_id, task.authority_epoch)
                and type(lease.reserved_input) is int
                and type(lease.reserved_output) is int
                and lease.reserved_input == policy.input_cap
                and lease.reserved_output == policy.output_cap
                and type(lease.expires_at) in (int, float)
                and math.isfinite(lease.expires_at)
                and lease.expires_at > self.clock())

    def execute(self, task: Task, worker: FixtureWorker) -> Outcome:
        policy = task.validate()
        if type(worker) is not FixtureWorker:
            raise ValueError("shadow_requires_exact_fixture_worker")
        # A repeat cannot execute even if a prior run failed; require a new task ID.
        if any(rec["task_id"] == task.task_id for rec in self.journal.records()):
            self._record(task, "duplicate_denied", reason="duplicate_task_id")
            return Outcome("denied", "duplicate_task_id", 0, 0, 0)
        self._record(task, "submitted")
        if not self.enabled or self.admission is None:
            reason = "shadow_disabled" if not self.enabled else "admission_unavailable"
            self._record(task, "terminal", status="denied", reason=reason)
            return Outcome("denied", reason, 0, 0, 0)
        try:
            lease = self.admission.acquire(task, policy)
        except Exception:
            lease = None
        if not self._valid_lease(lease, task):
            self._record(task, "terminal", status="denied", reason="lease_denied")
            return Outcome("denied", "lease_denied", 0, 0, 0)
        # No task events may be requested before an admitted receipt is durable.
        total_in, total_out, steps = 0, 0, 0
        status, reason = "cancelled", "missing_finish"
        try:
            self._record(task, "admitted", lease_id=lease.lease_id,
                         reserved_input=lease.reserved_input, reserved_output=lease.reserved_output)
            events = iter(worker.events(task))
            while True:
                # Renew before advancing a potentially streaming worker.
                # An actual Mercury transport must obey the same pre-next gate.
                try:
                    renewed = self.admission.renew(lease, task)
                except Exception:
                    renewed = None
                if not self._valid_lease(renewed, task) or renewed.lease_id != lease.lease_id:
                    reason = "renewal_denied"
                    break
                lease = renewed
                try:
                    event = next(events)
                except StopIteration:
                    break
                if not isinstance(event, Event) or event.model != task.model:
                    reason = "event_model_mismatch"
                    break
                if event.kind == "provider_error":
                    if event.error_status in TRIP_STATUSES:
                        self.admission.trip(task, event.error_status)
                    reason = "provider_error"
                    break
                if event.kind == "finish":
                    if steps < 1 or total_out < 1:
                        reason = "missing_usage_evidence"
                        break
                    status, reason = "completed", "mock_finished"
                    break
                if event.kind != "usage":
                    reason = "unknown_event"
                    break
                if (type(event.input_tokens) is not int or type(event.output_tokens) is not int
                        or event.input_tokens < 0 or event.output_tokens < 0):
                    reason = "invalid_token_event"
                    break
                if (steps + 1 > policy.step_cap or
                        total_in + event.input_tokens > policy.input_cap or
                        total_out + event.output_tokens > policy.output_cap):
                    reason = "budget_exceeded"
                    break
                steps += 1
                total_in += event.input_tokens
                total_out += event.output_tokens
                self._record(task, "usage", input_tokens=total_in,
                                    output_tokens=total_out, steps=steps)
        except CustodyError:
            raise
        except Exception:
            reason = "mock_runtime_failure"
        finally:
            if status != "completed":
                try:
                    worker.cancel()
                except Exception:
                    pass
            try:
                released = self.admission.release(lease, task)
            except Exception:
                released = False
            if not released:
                status, reason = "cancelled", "release_unverified"
            self._record(task, "terminal", status=status, reason=reason,
                                input_tokens=total_in, output_tokens=total_out, steps=steps)
        return Outcome(status, reason, total_in, total_out, steps)


def objective_digest(text: str) -> str:
    """Compute the digest before passing a task to the adapter; never persist text."""
    return sha256(text.encode("utf-8")).hexdigest()


class MockCustodyWitness:
    """Injected in-memory fixture acknowledgement, NOT independent or WORM custody."""

    def __init__(self, *, fail_on: frozenset[str] = frozenset()):
        self.fail_on = fail_on
        self.receipts: list[str] = []

    def acknowledge(self, receipt: dict) -> bool:
        if receipt["event"] in self.fail_on:
            return False
        unsigned = {k: v for k, v in receipt.items() if k != "event_hash"}
        expected = sha256(_canon(unsigned).encode()).hexdigest()
        if receipt["event_hash"] != expected or receipt["event_hash"] in self.receipts:
            return False
        self.receipts.append(receipt["event_hash"])
        return True


class MockSharedAuthority:
    """SQLite fixture-only physical-group lease gate with atomic idempotency.

    Local trusted filesystem only. No authentication, provider SDK, network, or
    production authority. Every task ID is single-use even after a denial.
    """

    def __init__(self, path: Path, *, clock=None, ttl: float = 30.0,
                 input_capacity: int = 6000, output_capacity: int = 900):
        self.path = Path(path)
        self.clock = clock or time.time
        self.ttl = ttl
        self.input_capacity = input_capacity
        self.output_capacity = output_capacity
        self.trip_statuses: list[int] = []
        if (self.path.is_symlink() or self.path.parent.is_symlink()
                or ttl <= 0 or input_capacity < 0 or output_capacity < 0):
            raise ValueError("unsafe_mock_authority_config")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(fd)
        os.close(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("unsafe_mock_authority_file")
        db = self._connect()
        try:
            db.execute("CREATE TABLE IF NOT EXISTS attempts("
                       "task_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS leases("
                       "lease_id TEXT PRIMARY KEY, task_id TEXT UNIQUE NOT NULL,"
                       "node_id TEXT NOT NULL, role TEXT NOT NULL,"
                       "attempt_id TEXT NOT NULL, epoch INTEGER NOT NULL,"
                       "model TEXT NOT NULL, group_id TEXT NOT NULL,"
                       "input_reserved INTEGER NOT NULL, output_reserved INTEGER NOT NULL,"
                       "expires_at REAL NOT NULL, status TEXT NOT NULL)")
        finally:
            db.close()

    def _connect(self):
        # Python's context manager commits/rolls back, but callers must close.
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def acquire(self, task: Task, policy: Specialist) -> Lease | None:
        if policy != task.validate():
            raise ValueError("mock_policy_mismatch")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            # Duplicate task IDs are denied atomically across threads/processes.
            if db.execute("SELECT 1 FROM attempts WHERE task_id=?", (task.task_id,)).fetchone():
                db.execute("COMMIT")
                return None
            db.execute("INSERT INTO attempts VALUES (?,?)", (task.task_id, "denied"))
            now = self.clock()
            busy = db.execute(
                "SELECT 1 FROM leases WHERE group_id=? AND status='active' AND expires_at>?",
                (task.group, now)
            ).fetchone()
            charged_in, charged_out = db.execute(
                "SELECT COALESCE(SUM(input_reserved),0), COALESCE(SUM(output_reserved),0)"
                " FROM leases WHERE group_id=?", (task.group,)
            ).fetchone()
            # A released fixture reservation is conservatively still charged.
            # No mock counter can claim vendor credits were refunded.
            if (busy or charged_in + policy.input_cap > self.input_capacity
                    or charged_out + policy.output_cap > self.output_capacity):
                db.execute("COMMIT")
                return None
            lease_id = "mock-" + uuid.uuid4().hex
            expiry = now + self.ttl
            db.execute("INSERT INTO leases VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (lease_id, task.task_id, task.node_id, task.role, task.attempt_id,
                        task.authority_epoch, task.model, task.group, policy.input_cap,
                        policy.output_cap, expiry, "active"))
            db.execute("UPDATE attempts SET status='admitted' WHERE task_id=?",
                       (task.task_id,))
            db.execute("COMMIT")
            return Lease(lease_id, task.group, expiry, task.task_id, task.node_id,
                         task.model, task.role, task.attempt_id, task.authority_epoch,
                         policy.input_cap, policy.output_cap)
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _transition(self, lease: Lease, task: Task, operation: str) -> Lease | bool | None:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT expires_at,status FROM leases WHERE lease_id=? AND task_id=?"
                " AND node_id=? AND role=? AND attempt_id=? AND epoch=?"
                " AND model=? AND group_id=? AND input_reserved=? AND output_reserved=?",
                (lease.lease_id, task.task_id, task.node_id, task.role, task.attempt_id,
                 task.authority_epoch, task.model, task.group, lease.reserved_input,
                 lease.reserved_output)
            ).fetchone()
            if not row or row[1] != "active" or row[0] <= self.clock():
                db.execute("COMMIT")
                return None if operation == "renew" else False
            if operation == "renew":
                new_expiry = self.clock() + self.ttl
                db.execute("UPDATE leases SET expires_at=? WHERE lease_id=?",
                           (new_expiry, lease.lease_id))
                result = Lease(lease.lease_id, lease.group, new_expiry, task.task_id,
                               task.node_id, task.model, task.role, task.attempt_id,
                               task.authority_epoch, lease.reserved_input,
                               lease.reserved_output)
            else:
                db.execute("UPDATE leases SET status='released' WHERE lease_id=?",
                           (lease.lease_id,))
                result = True
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def renew(self, lease: Lease, task: Task) -> Lease | None:
        return self._transition(lease, task, "renew")

    def release(self, lease: Lease, task: Task) -> bool:
        return bool(self._transition(lease, task, "release"))

    def revoke(self, lease_id: str) -> None:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE leases SET status='revoked' WHERE lease_id=?", (lease_id,))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def trip(self, task: Task, status: int) -> None:
        if status in TRIP_STATUSES:
            self.trip_statuses.append(status)


class FakeProviderCallSite:
    """Simulates provider-call-site admission. Never performs generation."""

    def __init__(self, authority: MockSharedAuthority, journal: Journal,
                 witness: MockCustodyWitness):
        self.authority = authority
        self.journal = journal
        self.witness = witness
        self.synthetic_call_count = 0

    def request(self, *, task: Task, lease: Lease, ingress: str) -> bool:
        # No ingress (including webhook, cron, peer, replay) may self-authorize.
        if ingress != "router":
            return False
        task.validate()
        if not isinstance(lease, Lease):
            return False
        policy = SPECIALISTS[task.role]
        if (lease.group != task.group or lease.task_id != task.task_id
                or lease.node_id != task.node_id or lease.model != task.model
                or lease.role != task.role or lease.attempt_id != task.attempt_id
                or lease.authority_epoch != task.authority_epoch
                or lease.reserved_input != policy.input_cap
                or lease.reserved_output != policy.output_cap):
            return False
        try:
            renewed = self.authority.renew(lease, task)
            if renewed is None or renewed.lease_id != lease.lease_id:
                return False
            receipt = self.journal.append(task, "provider_call_fixture",
                                          lease_id=lease.lease_id,
                                          reserved_input=lease.reserved_input,
                                          reserved_output=lease.reserved_output)
            if not self.witness.acknowledge(receipt):
                return False
        except (CustodyError, sqlite3.Error):
            return False
        self.synthetic_call_count += 1
        return True
