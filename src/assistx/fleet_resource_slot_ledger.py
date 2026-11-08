"""Host-side, fail-closed SQLite slot fencing for a future trusted model gateway.

No grants are created here. A separate, independently authorized resource
owner must provision the private ledger and mediate ALL model inference through
one trusted gateway. This module neither performs inference nor authorizes a
Task; direct upstream access and cross-service COMMIT remain separate gates.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

MAX_SLOTS = 16
MAX_LEASE_MS = 30_000
MAX_EPOCH = (1 << 63) - 1
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:@/-]{0,127}\Z")


class SlotLeaseDenied(RuntimeError):
    """No effective authorization to reserve or use a resource slot."""


@dataclass(frozen=True)
class SlotLease:
    runtime_id: str
    generation: int
    slot: int
    epoch: int
    task_id: str
    claim_id: str
    token: str
    expires_at_ms: int


class FencedSlotLedger:
    """Transactional per-host resource slots. NEVER initializes its own grant.

    The ledger file and its parent must be owned by the effective gateway UID
    and not accessible to other users. The owner must separately authenticate
    incoming requests and own all upstream inference paths. A checksum marker
    in SQLite alone is NOT independent owner cryptographic verification.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        runtime_id: str,
        generation: int,
        capacity: int,
        owner_receipt_sha256: str,
    ) -> None:
        self.path = Path(path)
        if (
            not self.path.is_absolute()
            or not ID.fullmatch(runtime_id)
            or type(generation) is not int
            or generation <= 0
            or type(capacity) is not int
            or not 1 <= capacity <= MAX_SLOTS
            or type(owner_receipt_sha256) is not str
            or not HEX64.fullmatch(owner_receipt_sha256)
        ):
            raise SlotLeaseDenied("invalid_expected_resource_identity")
        self.runtime_id = runtime_id
        self.generation = generation
        self.capacity = capacity
        self.owner_receipt_sha256 = owner_receipt_sha256

    def _connection(self) -> sqlite3.Connection:
        try:
            path = self.path
            # Explicit file/parent custody. This does not establish a separate
            # OS principal; release requires one trusted owner gateway UID.
            parent = path.parent.lstat()
            file = path.lstat()
            if (
                not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o077
                or not stat.S_ISREG(file.st_mode)
                or file.st_uid != os.geteuid()
                or file.st_mode & 0o077
                or file.st_nlink != 1
                or path.is_symlink()
            ):
                raise SlotLeaseDenied("resource_ledger_custody_invalid")
            # The database must already exist; never create a new grant here.
            uri = "file:" + quote(str(path), safe="/") + "?mode=rw"
            conn = sqlite3.connect(uri, uri=True, timeout=2.0, isolation_level=None)
            conn.execute("PRAGMA busy_timeout=2000")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        except (OSError, sqlite3.DatabaseError) as exc:
            raise SlotLeaseDenied("resource_ledger_unavailable") from exc

    def _grant_and_slots(self, conn: sqlite3.Connection, now_ms: int) -> tuple[int, list[tuple]]:
        # Reject missing schema and unknown/mutated grant or slot layout.
        grant = conn.execute(
            "SELECT runtime_id,generation,capacity,owner_receipt_sha256,expires_at_ms,status,clock_highwater_ms "
            "FROM resource_grant WHERE id=1"
        ).fetchone()
        if grant is None:
            raise SlotLeaseDenied("resource_owner_grant_missing")
        runtime_id, generation, capacity, digest, expires, status, highwater = grant
        if (
            runtime_id != self.runtime_id
            or type(generation) is not int
            or generation != self.generation
            or type(capacity) is not int
            or capacity != self.capacity
            or type(digest) is not str
            or digest != self.owner_receipt_sha256
            or status != "active"
            or type(expires) is not int
            or expires <= now_ms
        ):
            raise SlotLeaseDenied("resource_owner_grant_invalid_or_expired")
        if type(highwater) is not int or highwater < 0 or highwater > now_ms + 2_500:
            raise SlotLeaseDenied("resource_clock_rollback_or_corruption")
        # Persist maximum observed wall clock under BEGIN IMMEDIATE. A clock
        # rollback cannot silently resurrect a previously expired lease.
        conn.execute(
            "UPDATE resource_grant SET clock_highwater_ms=? WHERE id=1 AND clock_highwater_ms<?",
            (now_ms, now_ms),
        )
        slots = conn.execute(
            "SELECT slot,fence_epoch,token_sha256,task_id,claim_id,lease_expires_at_ms FROM resource_slot ORDER BY slot"
        ).fetchall()
        if len(slots) != self.capacity or any(
            type(r[0]) is not int
            or r[0] != index
            or type(r[1]) is not int
            or not 0 <= r[1] <= MAX_EPOCH
            or (r[2] is not None and (type(r[2]) is not str or not HEX64.fullmatch(r[2])))
            or type(r[5]) is not int
            for index, r in enumerate(slots)
        ):
            raise SlotLeaseDenied("resource_slot_layout_invalid")
        return expires, slots

    @staticmethod
    def _now(now_ms: int | None) -> int:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if type(now) is not int or now <= 0:
            raise SlotLeaseDenied("resource_clock_invalid")
        return now

    @staticmethod
    def _lease_limit(duration_ms: int) -> None:
        if type(duration_ms) is not int or not 1 <= duration_ms <= MAX_LEASE_MS:
            raise SlotLeaseDenied("unbounded_resource_lease")

    @staticmethod
    def _identity(task_id: str, claim_id: str) -> None:
        if (
            type(task_id) is not str
            or not ID.fullmatch(task_id)
            or type(claim_id) is not str
            or not ID.fullmatch(claim_id)
        ):
            raise SlotLeaseDenied("invalid_task_claim_identity")

    def _transact(self, callback):
        conn = self._connection()
        began = False
        try:
            conn.execute("BEGIN IMMEDIATE")  # SQLite cross-process write lock
            began = True
            result = callback(conn)
            conn.execute("COMMIT")
            began = False
            return result
        except SlotLeaseDenied:
            if began:
                conn.execute("ROLLBACK")
            raise
        except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError) as exc:
            if began:
                conn.execute("ROLLBACK")
            raise SlotLeaseDenied("resource_ledger_transaction_failed") from exc
        finally:
            conn.close()

    def acquire(
        self,
        task_id: str,
        claim_id: str,
        *,
        duration_ms: int = MAX_LEASE_MS,
        now_ms: int | None = None,
    ) -> SlotLease:
        self._identity(task_id, claim_id)
        self._lease_limit(duration_ms)
        now = self._now(now_ms)

        def apply(conn):
            grant_expiry, slots = self._grant_and_slots(conn, now)
            if any(r[2] is not None and r[3] == task_id and r[5] > now for r in slots):
                raise SlotLeaseDenied("duplicate_active_task_claim")
            for slot, epoch, token_hash, _task, _claim, expiry in slots:
                if token_hash is None or expiry <= now:
                    if epoch >= MAX_EPOCH:
                        raise SlotLeaseDenied("resource_fence_epoch_exhausted")
                    token = secrets.token_hex(32)
                    token_sha = hashlib.sha256(token.encode("ascii")).hexdigest()
                    until = min(now + duration_ms, grant_expiry)
                    if until <= now:
                        raise SlotLeaseDenied("resource_owner_grant_expired")
                    updated = conn.execute(
                        "UPDATE resource_slot SET fence_epoch=?, token_sha256=?, "
                        "task_id=?,claim_id=?,lease_expires_at_ms=? WHERE slot=? AND fence_epoch=?",
                        (epoch + 1, token_sha, task_id, claim_id, until, slot, epoch),
                    ).rowcount
                    if updated != 1:
                        raise SlotLeaseDenied("resource_slot_cas_lost")
                    return SlotLease(self.runtime_id, self.generation, slot, epoch + 1, task_id, claim_id, token, until)
            raise SlotLeaseDenied("resource_slots_exhausted")

        return self._transact(apply)

    def _validate_lease(self, lease: SlotLease, row: tuple, now: int) -> None:
        if (
            not isinstance(lease, SlotLease)
            or lease.runtime_id != self.runtime_id
            or lease.generation != self.generation
            or type(lease.slot) is not int
            or lease.slot < 0
            or lease.slot >= self.capacity
            or type(lease.epoch) is not int
            or lease.epoch <= 0
            or type(lease.token) is not str
            or not HEX64.fullmatch(lease.token)
            or type(lease.task_id) is not str
            or type(lease.claim_id) is not str
            or row[0] != lease.slot
            or row[1] != lease.epoch
            or row[3] != lease.task_id
            or row[4] != lease.claim_id
            or type(row[2]) is not str
            or type(lease.expires_at_ms) is not int
            or lease.expires_at_ms != row[5]
            or not hmac.compare_digest(row[2], hashlib.sha256(lease.token.encode("ascii")).hexdigest())
            or row[5] <= now
        ):
            raise SlotLeaseDenied("resource_slot_fence_or_lease_invalid")

    def validate_effect(self, lease: SlotLease, *, now_ms: int | None = None) -> None:
        """A control-plane check, not a durable inference-effect lock."""
        now = self._now(now_ms)

        def apply(conn):
            _, slots = self._grant_and_slots(conn, now)
            if type(lease.slot) is not int or not 0 <= lease.slot < len(slots):
                raise SlotLeaseDenied("resource_slot_index_invalid")
            self._validate_lease(lease, slots[lease.slot], now)

        self._transact(apply)

    def renew(self, lease: SlotLease, *, duration_ms: int = MAX_LEASE_MS, now_ms: int | None = None) -> SlotLease:
        self._lease_limit(duration_ms)
        now = self._now(now_ms)

        def apply(conn):
            grant_expiry, slots = self._grant_and_slots(conn, now)
            if type(lease.slot) is not int or not 0 <= lease.slot < len(slots):
                raise SlotLeaseDenied("resource_slot_index_invalid")
            row = slots[lease.slot]
            self._validate_lease(lease, row, now)
            until = min(now + duration_ms, grant_expiry)
            if until <= now:
                raise SlotLeaseDenied("resource_owner_grant_expired")
            count = conn.execute(
                "UPDATE resource_slot SET lease_expires_at_ms=? WHERE slot=? AND fence_epoch=? "
                "AND token_sha256=? AND lease_expires_at_ms>?",
                (until, lease.slot, lease.epoch, row[2], now),
            ).rowcount
            if count != 1:
                raise SlotLeaseDenied("resource_slot_renew_cas_lost")
            return SlotLease(
                lease.runtime_id,
                lease.generation,
                lease.slot,
                lease.epoch,
                lease.task_id,
                lease.claim_id,
                lease.token,
                until,
            )

        return self._transact(apply)

    def release(self, lease: SlotLease, *, now_ms: int | None = None) -> None:
        """Release only the same live claim/slot/epoch/token; never a successor."""
        now = self._now(now_ms)

        def apply(conn):
            _, slots = self._grant_and_slots(conn, now)
            if type(lease.slot) is not int or not 0 <= lease.slot < len(slots):
                raise SlotLeaseDenied("resource_slot_index_invalid")
            row = slots[lease.slot]
            self._validate_lease(lease, row, now)
            changed = conn.execute(
                "UPDATE resource_slot SET token_sha256=NULL,task_id=NULL,claim_id=NULL,"
                "lease_expires_at_ms=0 WHERE slot=? AND fence_epoch=? "
                "AND token_sha256=? AND lease_expires_at_ms>?",
                (lease.slot, lease.epoch, row[2], now),
            ).rowcount
            if changed != 1:
                raise SlotLeaseDenied("resource_slot_release_cas_lost")

        self._transact(apply)
