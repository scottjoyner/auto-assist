"""Research-only single-witness shadow quota fixture. NO provider, SDK, or dispatch.

This demonstrates why copied coordinator state MUST consult one external owner.
The SQLite file is itself copyable: this is not a production trust root.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import sqlite3

GROUP = "synthetic-upstream-one"
DENY = "DENY"


@dataclass(frozen=True)
class ShadowClient:
    node: str
    epoch: int
    connected: bool = True


class ShadowWitness:
    """One disposable shared SQLite fixture, deliberately not networked."""

    def __init__(self, path: str):
        self.path = str(Path(path))
        with self._db() as con:
            con.executescript("""
              CREATE TABLE IF NOT EXISTS authority(
                grp TEXT PRIMARY KEY, epoch INTEGER NOT NULL,
                leader TEXT NOT NULL, last_ms INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS seat(
                grp TEXT PRIMARY KEY, node TEXT NOT NULL,
                token TEXT NOT NULL, epoch INTEGER NOT NULL,
                expires_ms INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS seen(
                grp TEXT NOT NULL, nonce TEXT NOT NULL,
                PRIMARY KEY (grp, nonce));
            """)

    def _db(self):
        con = sqlite3.connect(self.path, timeout=1.0, isolation_level=None)
        con.execute("PRAGMA busy_timeout=1000")
        con.execute("PRAGMA synchronous=FULL")
        return con

    @staticmethod
    def _result(reason: str, *, token: str | None = None, shadow_effect=False):
        return {"decision": reason, "shadow_token": token,
                "shadow_effect_allowed": shadow_effect,
                "dispatch_authorized": False, "provider_calls": 0}

    def bootstrap(self, leader: str):
        """Fixture setup, NOT authenticated leadership election."""
        with self._db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT OR IGNORE INTO authority VALUES(?,?,?,?)",
                        (GROUP, 1, leader, 0))
            con.commit()

    def rotate(self, leader: str):
        """Synthetic trust-root operation, inaccessible to actual workers."""
        with self._db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT epoch FROM authority WHERE grp=?",
                              (GROUP,)).fetchone()
            if row is None:
                raise ValueError("uninitialized_fixture")
            epoch = row[0] + 1
            con.execute("UPDATE authority SET epoch=?,leader=? WHERE grp=?",
                        (epoch, leader, GROUP))
            con.commit()
            return epoch

    def acquire(self, client: ShadowClient, nonce: str, now_ms: int,
                ttl_ms: int = 1000, group: str = GROUP):
        if not client.connected:
            return self._result("witness_unreachable")
        if group != GROUP or type(now_ms) is not int or now_ms < 0 or \
           type(ttl_ms) is not int or not 1 <= ttl_ms <= 60000 or \
           not isinstance(nonce, str) or not 1 <= len(nonce) <= 100:
            return self._result("invalid_request")
        con = self._db()
        try:
            con.execute("BEGIN IMMEDIATE")
            auth = con.execute("SELECT epoch,leader,last_ms FROM authority WHERE grp=?",
                               (GROUP,)).fetchone()
            if auth is None or auth[0] != client.epoch or auth[1] != client.node:
                con.rollback()
                return self._result("stale_or_nonleader")
            if now_ms < auth[2]:
                con.rollback()
                return self._result("clock_rollback")
            try:
                con.execute("INSERT INTO seen VALUES(?,?)", (GROUP, nonce))
            except sqlite3.IntegrityError:
                con.rollback()
                return self._result("replay")
            current = con.execute("SELECT expires_ms FROM seat WHERE grp=?",
                                  (GROUP,)).fetchone()
            con.execute("UPDATE authority SET last_ms=? WHERE grp=?",
                        (now_ms, GROUP))
            if current and current[0] > now_ms:
                con.commit()
                return self._result("occupied")
            token = secrets.token_hex(16)
            con.execute("INSERT OR REPLACE INTO seat VALUES(?,?,?,?,?)",
                        (GROUP, client.node, token, client.epoch, now_ms+ttl_ms))
            con.commit()
            return self._result("SHADOW_ADMITTED_ONLY", token=token)
        except sqlite3.Error:
            con.rollback()
            return self._result("witness_error")
        finally:
            con.close()

    def check_effect(self, client: ShadowClient, token: str, now_ms: int):
        if not client.connected:
            return self._result("witness_unreachable")
        if type(now_ms) is not int or now_ms < 0:
            return self._result("invalid_request")
        try:
            with self._db() as con:
                auth = con.execute("SELECT epoch,leader,last_ms FROM authority WHERE grp=?",
                                   (GROUP,)).fetchone()
                seat = con.execute("SELECT node,token,epoch,expires_ms FROM seat WHERE grp=?",
                                   (GROUP,)).fetchone()
            valid = bool(auth and seat and auth[0] == client.epoch and
                         auth[1] == client.node and seat[0] == client.node and
                         seat[1] == token and seat[2] == client.epoch and
                         auth[2] <= now_ms < seat[3])
            return self._result("SHADOW_EFFECT_ONLY" if valid else "fenced",
                                shadow_effect=valid)
        except sqlite3.Error:
            return self._result("witness_error")

