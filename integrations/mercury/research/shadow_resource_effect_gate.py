"""Provider-free resource-side effect fence RESEARCH MODEL, never deploy.

Durable gate state is intentionally separate from coordinator snapshots.
The database itself is copyable and has no trusted clock, quorum, or key custody.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import sqlite3

GROUP = "fixture-upstream-single-slot"


@dataclass(frozen=True)
class Replica:
    node: str
    epoch: int
    connected: bool = True


def denied(reason: str) -> dict:
    return {"decision": "DENY", "reason": reason,
            "shadow_effect_allowed": False, "dispatch_authorized": False,
            "provider_calls": 0}


class ResourceEffectGate:
    """Simulates ONE protected downstream resource; no network/vendor action."""

    def __init__(self, path: str):
        self.path = str(Path(path))
        with self._connect() as conn:
            conn.executescript("""
              CREATE TABLE IF NOT EXISTS owner(
                grp TEXT PRIMARY KEY, epoch INTEGER NOT NULL,
                node TEXT NOT NULL, highwater_ms INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS seat(
                grp TEXT PRIMARY KEY, node TEXT NOT NULL,
                attempt TEXT NOT NULL, epoch INTEGER NOT NULL,
                token TEXT NOT NULL, expires_ms INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS nonces(
                grp TEXT NOT NULL, nonce TEXT NOT NULL,
                PRIMARY KEY(grp,nonce));
            """)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=2.0, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=2000")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def initialize_fixture(self, node: str):
        """Out-of-band fixture provisioning, NOT credential-bound authority."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO owner VALUES(?,?,?,?)",
                         (GROUP, 1, node, 0))
            conn.commit()

    def revoke_and_rotate_fixture(self, new_node: str) -> int:
        """An external privileged test action; never called by workers."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT epoch FROM owner WHERE grp=?",
                               (GROUP,)).fetchone()
            if row is None:
                raise ValueError("uninitialized")
            new_epoch = row[0] + 1
            conn.execute("UPDATE owner SET epoch=?,node=? WHERE grp=?",
                         (new_epoch, new_node, GROUP))
            conn.execute("DELETE FROM seat WHERE grp=?", (GROUP,))
            conn.commit()
        return new_epoch

    @staticmethod
    def _valid_time(value):
        return type(value) is int and 0 <= value <= 10**15

    @staticmethod
    def _answer(label, token=None):
        return {"decision": label, "shadow_token": token,
                "shadow_effect_allowed": False, "dispatch_authorized": False,
                "provider_calls": 0}

    def reserve(self, client: Replica, attempt: str, nonce: str, now_ms: int,
                ttl_ms: int = 1000, *, group: str = GROUP):
        if not isinstance(client, Replica) or not client.connected:
            return denied("resource_unreachable")
        if group != GROUP or not isinstance(attempt, str) or not attempt or \
           not isinstance(nonce, str) or not nonce or len(nonce) > 96 or \
           len(attempt) > 96 or not self._valid_time(now_ms) or \
           type(ttl_ms) is not int or not 1 <= ttl_ms <= 60000:
            return denied("invalid_request")
        conn = None
        try:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            auth = conn.execute("SELECT epoch,node,highwater_ms FROM owner WHERE grp=?",
                                (GROUP,)).fetchone()
            if auth is None or auth[0] != client.epoch or auth[1] != client.node:
                conn.rollback()
                return denied("stale_or_nonowner")
            if now_ms < auth[2]:
                conn.rollback()
                return denied("clock_rollback")
            try:
                conn.execute("INSERT INTO nonces VALUES(?,?)", (GROUP, nonce))
            except sqlite3.IntegrityError:
                conn.rollback()
                return denied("replayed_request")
            previous = conn.execute("SELECT expires_ms FROM seat WHERE grp=?",
                                    (GROUP,)).fetchone()
            conn.execute("UPDATE owner SET highwater_ms=? WHERE grp=?",
                         (now_ms, GROUP))
            if previous is not None and previous[0] > now_ms:
                conn.commit()
                return denied("slot_occupied")
            token = secrets.token_hex(16)
            conn.execute("INSERT OR REPLACE INTO seat VALUES(?,?,?,?,?,?)",
                         (GROUP, client.node, attempt, client.epoch,
                          token, now_ms + ttl_ms))
            conn.commit()
            return self._answer("SHADOW_RESERVED_ONLY", token)
        except sqlite3.Error:
            if conn is not None:
                conn.rollback()
            return denied("resource_error")
        finally:
            if conn is not None:
                conn.close()

    def check_effect(self, client: Replica, attempt: str, token: str,
                     now_ms: int, *, group: str = GROUP):
        """Always recheck downstream state; NEVER causes a real side effect."""
        if not isinstance(client, Replica) or not client.connected:
            return denied("resource_unreachable")
        if group != GROUP or not isinstance(attempt, str) or not attempt or \
           not isinstance(token, str) or not token or \
           not self._valid_time(now_ms):
            return denied("invalid_request")
        try:
            with self._connect() as conn:
                auth = conn.execute("SELECT epoch,node,highwater_ms FROM owner WHERE grp=?",
                                    (GROUP,)).fetchone()
                row = conn.execute(
                    "SELECT node,attempt,epoch,token,expires_ms FROM seat WHERE grp=?",
                    (GROUP,)).fetchone()
            valid = bool(auth is not None and row is not None and
                         auth[0] == client.epoch == row[2] and
                         auth[1] == client.node == row[0] and
                         row[1] == attempt and row[3] == token and
                         auth[2] <= now_ms < row[4])
            return {"decision": "SHADOW_EFFECT_ONLY" if valid else "DENY",
                    "reason": "fixture_effect_check" if valid else "fenced_or_expired",
                    "shadow_effect_allowed": valid,
                    "dispatch_authorized": False, "provider_calls": 0}
        except sqlite3.Error:
            return denied("resource_error")

