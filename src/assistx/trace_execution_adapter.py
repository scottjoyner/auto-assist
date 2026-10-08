"""Fail-closed, trace-first, built-in-only execution canary.

Not a shell adapter. This is deliberately limited to synthetic probes until
AssistX-backed authorization and cross-node trace validation are proven.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "assistx.trace-execution.v1"
IDENTITY = re.compile(r"^[a-zA-Z0-9_.:@-]{1,160}$")
ENABLED = {"1", "true", "yes", "on"}


class TraceDenied(Exception):
    """An unsafe, unaudited, or ambiguous execution was denied."""


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise TraceDenied(f"invalid_{field}")
    return value


class TraceReceiptStore:
    """Exclusive-locked, fsynced, tamper-evident journal, node-local only.

    Requires an already-provisioned private directory, never creates or
    follows symlinks, never writes the command payload or its raw output.
    """

    def __init__(self, root: str, *, node_id: str):
        if not isinstance(root, str) or not root:
            raise TraceDenied("trace_root_missing")
        self.root = Path(root)
        self.node_id = _identity(node_id, "node_id")
        try:
            st = self.root.lstat()
        except OSError as exc:
            raise TraceDenied("trace_root_unavailable") from exc
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise TraceDenied("trace_root_ownership_invalid")
        if st.st_mode & 0o077:
            raise TraceDenied("trace_root_permissions_unsafe")
        self.path = self.root / "journal.jsonl"

    @staticmethod
    def _verify_data(data: bytes) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        prev = "0" * 64
        if data and not data.endswith(b"\n"):
            raise TraceDenied("torn_audit_journal")
        for raw in data.splitlines():
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise TraceDenied("corrupt_audit_journal") from exc
            if not isinstance(record, dict):
                raise TraceDenied("corrupt_audit_journal")
            digest = record.pop("entry_hash", None)
            computed = hashlib.sha256(_canonical(record)).hexdigest()
            if (
                record.get("schema") != SCHEMA
                or record.get("seq") != len(records) + 1
                or record.get("prev_hash") != prev
                or digest != computed
            ):
                raise TraceDenied("audit_chain_invalid")
            record["entry_hash"] = digest
            records.append(record)
            prev = digest
        return records

    def verify(self) -> dict[str, Any]:
        records = self._read_locked(create=False)
        if any(row.get("node_id") != self.node_id for row in records):
            raise TraceDenied("audit_node_identity_mismatch")
        return {
            "ok": True,
            "records": len(records),
            "last_hash": records[-1]["entry_hash"] if records else None,
        }

    def snapshot(self, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        """Locked validated byte-exact snapshot; never repairs or truncates."""
        fd = self._open_fd(create=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if os.fstat(fd).st_size > max_bytes:
                raise TraceDenied("audit_snapshot_too_large")
            os.lseek(fd, 0, os.SEEK_SET)
            chunks = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > max_bytes:
                raise TraceDenied("audit_snapshot_too_large")
            records = self._verify_data(data)
            if any(row.get("node_id") != self.node_id for row in records):
                raise TraceDenied("audit_node_identity_mismatch")
            if not records:
                raise TraceDenied("audit_snapshot_empty")
            return data
        except OSError as exc:
            raise TraceDenied("audit_snapshot_read_failed") from exc
        finally:
            os.close(fd)

    def _open_fd(self, *, create: bool) -> int:
        flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
        if create:
            flags |= os.O_CREAT
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise TraceDenied("audit_open_failed") from exc
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or (st.st_mode & 0o077) or st.st_nlink != 1:
            os.close(fd)
            raise TraceDenied("audit_file_unsafe")
        return fd

    @staticmethod
    def _load(fd: int) -> list[dict[str, Any]]:
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return TraceReceiptStore._verify_data(b"".join(chunks))

    def _read_locked(self, *, create: bool) -> list[dict[str, Any]]:
        fd = self._open_fd(create=create)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return self._load(fd)
        except OSError as exc:
            raise TraceDenied("audit_io_failed") from exc
        finally:
            os.close(fd)

    def append(self, event: dict[str, Any], *, first: bool = False) -> dict[str, Any]:
        # First-preparation may create the journal; later writes must not
        # silently create a second journal if the original disappeared.
        fd = self._open_fd(create=first)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            existing = self._load(fd)
            if any(row.get("node_id") != self.node_id for row in existing):
                raise TraceDenied("audit_node_identity_mismatch")
            key = (event["task_id"], event["claim_id"])
            statuses = [row["event"] for row in existing if (row.get("task_id"), row.get("claim_id")) == key]
            if event["event"] == "prepared":
                if statuses:
                    raise TraceDenied("attempt_already_recorded")
            elif event["event"] == "completed":
                if statuses != ["prepared"]:
                    raise TraceDenied("attempt_not_prepared_or_completed")
            else:
                raise TraceDenied("unrecognized_audit_event")
            row = {
                "schema": SCHEMA,
                "seq": len(existing) + 1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "node_id": self.node_id,
                "prev_hash": existing[-1]["entry_hash"] if existing else "0" * 64,
                **event,
            }
            row["entry_hash"] = hashlib.sha256(_canonical(row)).hexdigest()
            raw = _canonical(row) + b"\n"
            os.lseek(fd, 0, os.SEEK_END)
            if os.write(fd, raw) != len(raw):
                raise TraceDenied("audit_short_write")
            os.fsync(fd)
            # Persist the name itself when the journal is initially created.
            if first and len(existing) == 0:
                dir_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            return row
        except OSError as exc:
            raise TraceDenied("audit_io_failed") from exc
        finally:
            os.close(fd)


def run_trace_probe(
    task: dict[str, Any],
    *,
    node_id: str,
    claim_id: str,
    audit_root: str,
    enabled: bool = False,
    signed_grant_sha256: str | None = None,
) -> dict[str, Any]:
    """Execute only synthetic probes AFTER durable pre-execution receipt.

    claim_id must be supplied by a successful AssistX claim response, not
    from the untrusted payload. No shell, network, or machine-changing work.
    """
    if not enabled:
        raise TraceDenied("trace_probe_disabled")
    task_id = _identity(task.get("task_id") or task.get("id"), "task_id")
    node_id = _identity(node_id, "node_id")
    claim_id = _identity(claim_id, "claim_id")
    target = _identity(task.get("target_agent_id"), "target_node_id")
    if target != node_id:
        raise TraceDenied("wrong_execution_node")
    payload = task.get("payload") or {}
    if not isinstance(payload, dict):
        raise TraceDenied("invalid_probe_payload")
    if set(payload) - {"command_id", "message"}:
        raise TraceDenied("unapproved_probe_fields")
    command = payload.get("command_id")
    if command not in {"probe.noop.v1", "probe.echo.v1"}:
        raise TraceDenied("unapproved_command_id")
    message = payload.get("message", "")
    if not isinstance(message, str) or len(message.encode("utf-8")) > 160:
        raise TraceDenied("invalid_probe_message")
    if command == "probe.noop.v1" and message:
        raise TraceDenied("noop_must_not_have_message")
    if signed_grant_sha256 is not None and (
        not isinstance(signed_grant_sha256, str)
        or len(signed_grant_sha256) != 64
        or any(c not in "0123456789abcdef" for c in signed_grant_sha256)
    ):
        raise TraceDenied("invalid_signed_grant_digest")
    store = TraceReceiptStore(audit_root, node_id=node_id)
    input_digest = _digest({"command_id": command, "message": message})
    prepared = store.append(
        {
            "event": "prepared",
            "task_id": task_id,
            "claim_id": claim_id,
            "command_id": command,
            "input_sha256": input_digest,
            **({"signed_grant_sha256": signed_grant_sha256} if signed_grant_sha256 else {}),
        },
        first=True,
    )
    result = {"kind": "noop"} if command == "probe.noop.v1" else {"kind": "echo", "message": message}
    completed = store.append(
        {
            "event": "completed",
            "task_id": task_id,
            "claim_id": claim_id,
            "command_id": command,
            "prepared_hash": prepared["entry_hash"],
            "output_sha256": _digest(result),
            "status": "DONE",
            **({"signed_grant_sha256": signed_grant_sha256} if signed_grant_sha256 else {}),
        }
    )
    verification = store.verify()
    if verification["last_hash"] != completed["entry_hash"]:
        raise TraceDenied("completion_receipt_unverified")
    return {
        "status": "DONE",
        "result": result,
        "trace": {
            "prepared_hash": prepared["entry_hash"],
            "completed_hash": completed["entry_hash"],
            "audit_schema": SCHEMA,
        },
    }
