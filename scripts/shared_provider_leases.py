#!/usr/bin/env python3
"""Prospective provider lease ledger; no network/API or generation side effects.

A single SQLite coordinator serializes cooperating clients on one host.
Not a distributed authority until authenticated remote transport and client
self-termination on lease loss are implemented and reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from contextlib import contextmanager
import json
import os
import pwd
import secrets
import sqlite3
import time

ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
DEFAULT_DB = ACCOUNT_HOME / ".local/state/auto-assist/provider-leases.sqlite3"


@dataclass(frozen=True)
class Policy:
    slots: int = 1
    requests_per_window: int = 2
    reserved_input_per_window: int = 40_000
    reserved_output_per_window: int = 4_000
    window_seconds: int = 900
    max_ttl_seconds: int = 120


# Exact, independently qualified provider groups only. An aggregator that
# might re-use one of these upstreams must NOT receive a separate quota.
DEFAULT_POLICIES = {"kilo": Policy()}
ALIASES = {"kilo": "kilo", "kilo_free": "kilo", "kilo_anonymous": "kilo"}


def _as_int(value: int, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid_reservation_argument")
    return value


class ProviderLeaseLedger:
    def __init__(self, db: Path = DEFAULT_DB, *, policies: dict[str, Policy] | None = None,
                 aliases: dict[str, str] | None = None):
        self.db = Path(db)
        self.policies = DEFAULT_POLICIES.copy() if policies is None else dict(policies)
        self.aliases = ALIASES.copy() if aliases is None else dict(aliases)
        for provider, policy in self.policies.items():
            if not provider or not isinstance(policy, Policy) or policy.slots < 1:
                raise ValueError("invalid_policy")
        self.db.parent.mkdir(parents=True, exist_ok=True)

        if self.db.is_symlink():
            raise ValueError('unsafe_db_symlink')
        if self.db.exists() and (self.db.stat().st_uid != os.getuid()
                                 or self.db.stat().st_mode & 0o077):
            raise ValueError('untrusted_db')
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS leases (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    provider TEXT NOT NULL, client TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL,
                    released REAL, input_reserved INTEGER NOT NULL,
                    output_reserved INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS leases_active_idx
                    ON leases(provider,expires,released);
                CREATE TABLE IF NOT EXISTS circuits (
                    provider TEXT PRIMARY KEY, blocked_until REAL NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    epoch REAL NOT NULL, event TEXT NOT NULL,
                    provider TEXT NOT NULL, reason TEXT NOT NULL,
                    subject TEXT, previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
            """)
        os.chmod(self.db, 0o600)

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(str(self.db), timeout=3, isolation_level=None)
        con.execute("PRAGMA busy_timeout=3000")
        try:
            yield con
        finally:
            con.close()

    def _group(self, provider: str) -> str | None:
        group = self.aliases.get(provider)
        return group if group in self.policies else None

    @staticmethod
    def _event(con, now: float, event: str, group: str,
               reason: str, subject: str | None = None) -> None:
        previous = con.execute("SELECT event_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = previous[0] if previous else "0" * 64
        payload = json.dumps([now, event, group, reason, subject, prev_hash],
                             separators=(",", ":"), ensure_ascii=True)
        digest = sha256(payload.encode()).hexdigest()
        con.execute("INSERT INTO audit(epoch,event,provider,reason,subject,previous_hash,event_hash) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (now, event, group, reason, subject, prev_hash, digest))

    def acquire(self, provider: str, client: str, request_key: str, *,
                input_reserved: int = 1, output_reserved: int = 1,
                ttl_seconds: int = 90, now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        group = self._group(provider)
        if group is None:
            return {"granted": False, "reason": "unknown_or_ambiguous_provider"}
        policy = self.policies[group]
        if not isinstance(client, str) or not 1 <= len(client) <= 128:
            return {"granted": False, "reason": "invalid_client"}
        if not isinstance(request_key, str) or not 8 <= len(request_key) <= 128:
            return {"granted": False, "reason": "invalid_request_key"}
        try:
            input_reserved = _as_int(input_reserved, minimum=1, maximum=policy.reserved_input_per_window)
            output_reserved = _as_int(output_reserved, minimum=1, maximum=policy.reserved_output_per_window)
            ttl_seconds = _as_int(ttl_seconds, minimum=5, maximum=policy.max_ttl_seconds)
        except ValueError:
            return {"granted": False, "reason": "invalid_reservation"}
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                circuit = con.execute("SELECT blocked_until FROM circuits WHERE provider=?", (group,)).fetchone()
                if circuit and circuit[0] > now:
                    self._event(con, now, "deny", group, "circuit_open")
                    con.commit()
                    return {"granted": False, "reason": "circuit_open",
                            "retry_after": round(circuit[0] - now, 2)}
                existing = con.execute(
                    "SELECT id,provider,client,expires,released,input_reserved,output_reserved "
                    "FROM leases WHERE request_key=?", (request_key,)).fetchone()
                if existing is not None:
                    lid, p, owner, expires, released, reserved_in, reserved_out = existing
                    same = (p == group and owner == client and input_reserved == reserved_in
                            and output_reserved == reserved_out)
                    if same and released is None and expires > now:
                        self._event(con, now, "idempotent", group, "existing_active_lease", lid)
                        con.commit()
                        return {"granted": True, "reason": "existing_active_lease",
                                "lease_id": lid, "expires_at": expires, "provider": group}
                    self._event(con, now, "deny", group, "request_key_already_used")
                    con.commit()
                    return {"granted": False, "reason": "request_key_already_used"}
                active = con.execute(
                    "SELECT COUNT(*) FROM leases WHERE provider=? AND released IS NULL AND expires>?",
                    (group, now)).fetchone()[0]
                count, total_in, total_out = con.execute(
                    "SELECT COUNT(*), COALESCE(SUM(input_reserved),0), "
                    "COALESCE(SUM(output_reserved),0) FROM leases WHERE provider=? AND created>?",
                    (group, now-policy.window_seconds)).fetchone()
                if active >= policy.slots or count >= policy.requests_per_window:
                    reason = "slot_full" if active >= policy.slots else "request_window_full"
                elif total_in + input_reserved > policy.reserved_input_per_window:
                    reason = "input_window_full"
                elif total_out + output_reserved > policy.reserved_output_per_window:
                    reason = "output_window_full"
                else:
                    reason = None
                if reason:
                    self._event(con, now, "deny", group, reason)
                    con.commit()
                    return {"granted": False, "reason": reason}
                lid = secrets.token_hex(24)
                expires = now + ttl_seconds
                con.execute(
                    "INSERT INTO leases VALUES(?,?,?,?,?,?,?,?,?)",
                    (lid, request_key, group, client, now, expires, None,
                     input_reserved, output_reserved))
                self._event(con, now, "acquire", group, "granted", lid)
                con.commit()
                return {"granted": True, "reason": "new_lease",
                        "lease_id": lid, "expires_at": expires, "provider": group}
        except sqlite3.Error:
            return {"granted": False, "reason": "ledger_unavailable"}

    def release(self, lease_id: str, client: str, *, now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT provider,client,released FROM leases WHERE id=?",
                                  (lease_id,)).fetchone()
                if not row or row[1] != client:
                    con.commit()
                    return {"released": False, "reason": "unknown_lease_or_owner"}
                if row[2] is not None:
                    con.commit()
                    return {"released": True, "reason": "already_released"}
                con.execute("UPDATE leases SET released=? WHERE id=?", (now, lease_id))
                self._event(con, now, "release", row[0], "released", lease_id)
                con.commit()
                return {"released": True, "reason": "released"}
        except sqlite3.Error:
            return {"released": False, "reason": "ledger_unavailable"}

    def renew(self, lease_id: str, client: str, *, ttl_seconds: int = 90,
              now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT provider,client,expires,released FROM leases WHERE id=?",
                                  (lease_id,)).fetchone()
                if not row or row[1] != client or row[3] is not None or row[2] <= now:
                    con.commit()
                    return {"renewed": False, "reason": "inactive_or_unknown_lease"}
                group = row[0]
                _as_int(ttl_seconds, minimum=5, maximum=self.policies[group].max_ttl_seconds)
                circuit = con.execute("SELECT blocked_until FROM circuits WHERE provider=?", (group,)).fetchone()
                if circuit and circuit[0] > now:
                    self._event(con, now, "deny_renew", group, "circuit_open", lease_id)
                    con.commit()
                    return {"renewed": False, "reason": "circuit_open"}
                expires = max(row[2], now + ttl_seconds)
                con.execute("UPDATE leases SET expires=? WHERE id=?", (expires, lease_id))
                self._event(con, now, "renew", group, "extended", lease_id)
                con.commit()
                return {"renewed": True, "reason": "extended", "expires_at": expires}
        except (sqlite3.Error, ValueError, KeyError):
            return {"renewed": False, "reason": "invalid_or_unavailable"}

    def trip(self, provider: str, status_code: int, *,
             retry_after: int = 0, now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        group = self._group(provider)
        if group is None:
            return {"tripped": False, "reason": "unknown_or_ambiguous_provider"}
        if type(status_code) is not int or status_code not in (401, 402, 403, 429, 503):
            return {"tripped": False, "reason": "not_a_trip_status"}
        if type(retry_after) is not int or retry_after < 0:
            return {"tripped": False, "reason": "invalid_retry_after"}
        delay = max(retry_after, 86400 if status_code in (401, 402, 403) else 3600)
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                old = con.execute("SELECT blocked_until FROM circuits WHERE provider=?", (group,)).fetchone()
                until = max(now + delay, old[0] if old else 0)
                con.execute("INSERT INTO circuits VALUES(?,?,?) ON CONFLICT(provider) DO UPDATE "
                            "SET blocked_until=excluded.blocked_until,reason=excluded.reason",
                            (group, until, f"http_{status_code}"))
                self._event(con, now, "trip", group, f"http_{status_code}")
                con.commit()
                return {"tripped": True, "blocked_until": until, "provider": group}
        except sqlite3.Error:
            return {"tripped": False, "reason": "ledger_unavailable"}

    def snapshot(self, *, now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        with self._connect() as con:
            result = {}
            for group in self.policies:
                active = con.execute(
                    "SELECT COUNT(*) FROM leases WHERE provider=? AND released IS NULL AND expires>?",
                    (group, now)).fetchone()[0]
                circuit = con.execute("SELECT blocked_until FROM circuits WHERE provider=?", (group,)).fetchone()
                result[group] = {"active": active, "slots": self.policies[group].slots,
                                 "circuit_open": bool(circuit and circuit[0] > now)}
            return result

    def verify_audit(self) -> bool:
        prev = "0" * 64
        with self._connect() as con:
            for epoch, event, group, reason, subject, prior, digest in con.execute(
                "SELECT epoch,event,provider,reason,subject,previous_hash,event_hash "
                "FROM audit ORDER BY seq"
            ):
                payload = json.dumps([epoch, event, group, reason, subject, prev],
                                     separators=(",", ":"), ensure_ascii=True)
                if prior != prev or sha256(payload.encode()).hexdigest() != digest:
                    return False
                prev = digest
        return True


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = p.parse_args()
    ledger = ProviderLeaseLedger(a.db)
    print(json.dumps({"schema": "provider-lease-status/v1",
                      "providers": ledger.snapshot(),
                      "audit_integrity": ledger.verify_audit()}, sort_keys=True))
