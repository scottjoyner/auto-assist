"""Atomic shadow effect ledger; local research ONLY, no provider calls or dispatch."""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from shadow_resource_effect_gate import GROUP, Replica, ResourceEffectGate

TOKEN = re.compile(r"[0-9a-f]{32}\Z")
ID = re.compile(r"[A-Za-z0-9_.:-]{1,96}\Z")


def verdict(decision, reason, recorded=False):
    return {"decision": decision, "reason": reason, "shadow_ledger_recorded": recorded,
            "dispatch_authorized": False, "provider_calls": 0, "real_effects": 0}


def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AtomicShadowEffectLedger:
    """One copyable SQLite resource; no externally protected authority."""

    def __init__(self, gate: ResourceEffectGate):
        self.gate = gate
        with gate._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS shadow_effect_ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, grp TEXT NOT NULL,
                effect_id TEXT NOT NULL, node TEXT NOT NULL,
                attempt TEXT NOT NULL, epoch INTEGER NOT NULL,
                committed_ms INTEGER NOT NULL, token_sha256 TEXT NOT NULL,
                previous_sha256 TEXT NOT NULL, receipt_sha256 TEXT NOT NULL,
                UNIQUE(grp, effect_id), UNIQUE(grp, token_sha256))""")

    def record(self, client: Replica, attempt: str, token: str,
               effect_id: str, now_ms: int, *, group: str = GROUP):
        """Validate and insert synthetic marker in one transaction, never perform effect."""
        if not isinstance(client, Replica) or not client.connected:
            return verdict("DENY", "resource_unreachable")
        if group != GROUP or type(client.epoch) is not int or \
           not ResourceEffectGate._valid_time(now_ms) or \
           not all(isinstance(v, str) and ID.fullmatch(v)
                   for v in (client.node, attempt, effect_id)) or \
           not isinstance(token, str) or not TOKEN.fullmatch(token):
            return verdict("DENY", "invalid_request")
        con = None
        try:
            con = self.gate._connect()
            con.execute("BEGIN IMMEDIATE")
            auth = con.execute(
                "SELECT epoch,node,highwater_ms FROM owner WHERE grp=?", (GROUP,)
            ).fetchone()
            seat = con.execute(
                "SELECT node,attempt,epoch,token,expires_ms FROM seat WHERE grp=?",
                (GROUP,)).fetchone()
            if not auth or not seat or auth[0] != client.epoch or \
               seat[2] != client.epoch or auth[1] != client.node or \
               seat[0] != client.node or seat[1] != attempt or \
               seat[3] != token or not auth[2] <= now_ms < seat[4]:
                con.rollback()
                return verdict("DENY", "fenced_expired_or_clock_rollback")
            if con.execute("SELECT 1 FROM shadow_effect_ledger WHERE grp=? AND effect_id=?",
                           (GROUP, effect_id)).fetchone():
                con.rollback()
                return verdict("QUARANTINE", "ambiguous_prior_ack_no_replay")
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            if con.execute(
                "SELECT 1 FROM shadow_effect_ledger WHERE grp=? AND token_sha256=?",
                (GROUP, token_hash)).fetchone():
                con.rollback()
                return verdict("QUARANTINE", "lease_already_consumed_no_extra_effect")
            prev = con.execute(
                "SELECT receipt_sha256 FROM shadow_effect_ledger ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prior = prev[0] if prev else "0" * 64
            entry = {"group": GROUP, "effect_id": effect_id, "node": client.node,
                     "attempt": attempt, "epoch": client.epoch, "committed_ms": now_ms,
                     "token_sha256": token_hash,
                     "previous_sha256": prior}
            con.execute("""INSERT INTO shadow_effect_ledger
                (grp,effect_id,node,attempt,epoch,committed_ms,token_sha256,
                 previous_sha256,receipt_sha256) VALUES(?,?,?,?,?,?,?,?,?)""",
                (GROUP, effect_id, client.node, attempt, client.epoch, now_ms,
                 entry["token_sha256"], prior, sha(entry)))
            con.execute("UPDATE owner SET highwater_ms=? WHERE grp=?", (now_ms, GROUP))
            con.commit()
            return verdict("SHADOW_RECORDED_ONLY", "atomic_fixture_commit", True)
        except sqlite3.Error:
            if con is not None:
                try:
                    con.rollback()
                except sqlite3.Error:
                    pass
            return verdict("QUARANTINE", "transaction_outcome_unknown")
        finally:
            if con is not None:
                con.close()

    def local_check(self):
        """Detect some tampering but NEVER prove independent custody or rollback."""
        try:
            with self.gate._connect() as con:
                rows = con.execute("""SELECT grp,effect_id,node,attempt,epoch,
                    committed_ms,token_sha256,previous_sha256,receipt_sha256
                    FROM shadow_effect_ledger ORDER BY seq""").fetchall()
            prev = "0" * 64
            for row in rows:
                entry = dict(zip(("group", "effect_id", "node", "attempt", "epoch",
                                  "committed_ms", "token_sha256", "previous_sha256"),
                                 row[:8]))
                if row[7] != prev or row[8] != sha(entry):
                    return {"local_chain_ok": False, "count": len(rows),
                            "independent_custody_verified": False}
                prev = row[8]
            return {"local_chain_ok": True, "count": len(rows),
                    "head": prev, "independent_custody_verified": False}
        except sqlite3.Error:
            return {"local_chain_ok": False, "count": 0,
                    "independent_custody_verified": False}

