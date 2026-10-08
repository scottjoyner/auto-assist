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
import os
import re
import stat
import time

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

    def validate(self) -> Specialist:
        if not isinstance(self.task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{7,79}", self.task_id):
            raise ValueError("invalid_task_id")
        if self.role not in SPECIALISTS:
            raise ValueError("unknown_specialist")
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

    def append(self, task: Task, kind: str, **fields: object) -> None:
        # Only these keys are serialized; never prompts, source files, tool output or keys.
        allowed = {"reason", "status", "input_tokens", "output_tokens", "steps"}
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
        except OSError as exc:
            raise CustodyError("trace_write_unavailable") from exc


class MercuryShadowAdapter:
    """An explicitly enabled simulation; no function here calls Mercury or a provider."""

    def __init__(self, journal: Journal, admission: Admission | None = None,
                 *, enabled: bool = False, clock=None):
        self.journal = journal
        self.admission = admission
        self.enabled = enabled
        self.clock = clock or time.time

    def _valid_lease(self, lease: Lease | None, task: Task) -> bool:
        return (isinstance(lease, Lease)
                and isinstance(lease.lease_id, str)
                and bool(re.fullmatch(r"[A-Za-z0-9_-]{8,128}", lease.lease_id))
                and lease.group == task.group
                and type(lease.expires_at) in (int, float)
                and lease.expires_at > self.clock())

    def execute(self, task: Task, worker: FixtureWorker) -> Outcome:
        policy = task.validate()
        if type(worker) is not FixtureWorker:
            raise ValueError("shadow_requires_exact_fixture_worker")
        # A repeat cannot execute even if a prior run failed; require a new task ID.
        if any(rec["task_id"] == task.task_id for rec in self.journal.records()):
            self.journal.append(task, "duplicate_denied", reason="duplicate_task_id")
            return Outcome("denied", "duplicate_task_id", 0, 0, 0)
        self.journal.append(task, "submitted")
        if not self.enabled or self.admission is None:
            reason = "shadow_disabled" if not self.enabled else "admission_unavailable"
            self.journal.append(task, "terminal", status="denied", reason=reason)
            return Outcome("denied", reason, 0, 0, 0)
        try:
            lease = self.admission.acquire(task, policy)
        except Exception:
            lease = None
        if not self._valid_lease(lease, task):
            self.journal.append(task, "terminal", status="denied", reason="lease_denied")
            return Outcome("denied", "lease_denied", 0, 0, 0)
        # No task events may be requested before an admitted receipt is durable.
        total_in, total_out, steps = 0, 0, 0
        status, reason = "cancelled", "missing_finish"
        try:
            self.journal.append(task, "admitted")
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
                self.journal.append(task, "usage", input_tokens=total_in,
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
            self.journal.append(task, "terminal", status=status, reason=reason,
                                input_tokens=total_in, output_tokens=total_out, steps=steps)
        return Outcome(status, reason, total_in, total_out, steps)


def objective_digest(text: str) -> str:
    """Compute the digest before passing a task to the adapter; never persist text."""
    return sha256(text.encode("utf-8")).hexdigest()