"""RESEARCH ONLY: one PostgreSQL writer as a non-expiring query-reservation authority.

This *centralized single-primary* experiment is not distributed HA/consensus.
All connections are limited to an inspected disposable internal Docker network.
No API or production route imports this module. Persistent state loss, missing
tables, wrong external epoch or transport loss must fail CLOSED, never rearm.
No TTL or worker-process-death reclamation is permitted.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from assistx.trace_durable_ledger_research import _canonical, RECEIPT_VERSION, CLOSED

NAME = "assistx-trace-authority-pg-20261009"
NETWORK = "assistx-trace-authority-net-20261009"
EPOCH_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
QUERY_REF_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
SCHEMA = "assistx-physical-pg-research-v1"
DDL = """
CREATE TABLE research_trace_meta (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    epoch text NOT NULL,
    schema text NOT NULL,
    capacity integer NOT NULL CHECK (capacity BETWEEN 1 AND 16)
);
CREATE TABLE research_trace_slots (
    token text PRIMARY KEY,
    epoch text NOT NULL,
    query_ref text NOT NULL UNIQUE,
    admitted_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
"""


@dataclass(frozen=True)
class Admission:
    token: str | None
    occupancy: int | None
    reason: str


def _valid_epoch(epoch):
    return isinstance(epoch, str) and bool(EPOCH_RE.fullmatch(epoch))


def _inspect(*cmd):
    p = subprocess.run(["docker", *cmd], capture_output=True, text=True,
                       timeout=8, check=True)
    return json.loads(p.stdout)


def checked_disposable_dsn() -> str:
    """Runtime may not supply its own hostname or endpoint."""
    if os.getenv("ASSISTX_TRACE_PG_DISPOSABLE_RESEARCH") != "1":
        raise RuntimeError("EXPLICIT_RESEARCH_OPT_IN_REQUIRED")
    c = _inspect("inspect", NAME)
    n = _inspect("network", "inspect", NETWORK)
    if len(c) != 1 or len(n) != 1:
        raise RuntimeError("MISSING_DISPOSABLE_AUTHORITY")
    obj, net = c[0], n[0]
    h = obj["HostConfig"]
    ip = obj["NetworkSettings"]["Networks"][NETWORK]["IPAddress"]
    if (obj["Name"] != "/" + NAME or not obj["State"]["Running"]
        or obj["Config"]["Image"] != "postgres:17-alpine"
        or h["NetworkMode"] != NETWORK
        or set(obj["NetworkSettings"]["Networks"]) != {NETWORK}
        or not net["Internal"] or h.get("PortBindings")
        or any(m.get("Type") == "bind" for m in obj.get("Mounts", []))
        or h["NanoCpus"] > 1_000_000_000
        or not 0 < h["Memory"] <= 805_306_368
        or not ip.startswith("172.") or not ip.endswith(".2")
        or "POSTGRES_HOST_AUTH_METHOD=trust" not in obj["Config"]["Env"]):
        raise RuntimeError("UNSAFE_AUTHORITY_FIXTURE")
    return f"postgresql://postgres@{ip}:5432/postgres"


def _open(dsn):
    # Only optional research dependency; no production import side effects.
    import psycopg
    return psycopg.connect(dsn, connect_timeout=3, autocommit=False,
                           options="-c statement_timeout=3000")


def bootstrap_once(dsn, epoch, capacity):
    """DISPOSABLE FIXTURE ONLY: explicit one-time schema creation.

    A failed/absent production cluster is NEVER allowed to bootstrap itself
    from an API request. The caller must independently pin an epoch.
    """
    if not _valid_epoch(epoch) or type(capacity) is not int or not 1 <= capacity <= 16:
        raise ValueError("INVALID_GENESIS")
    if dsn != checked_disposable_dsn():
        raise RuntimeError("FOREIGN_AUTHORITY_DENIED")
    with _open(dsn) as db:
        with db.transaction():
            # Do not modify an existing ledger or repair a dropped slot table.
            tables = db.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename LIKE 'research_trace_%'"
            ).fetchall()
            if tables:
                raise RuntimeError("REFUSE_TO_REARM_EXISTING_AUTHORITY")
            db.execute(DDL)
            db.execute("INSERT INTO research_trace_meta VALUES (true,%s,%s,%s)",
                       (epoch, SCHEMA, capacity))


class PostgresTraceAuthority:
    """Serialized global occupancy for workers sharing ONE durable primary.

    Caller may verify an independently produced Ed25519 receipt but must NOT
    hold its private signing key. A signature is no proof of server termination.
    """

    def __init__(self, expected_epoch, verifier_public_key: bytes):
        if not _valid_epoch(expected_epoch) or type(verifier_public_key) is not bytes or len(verifier_public_key) != 32:
            raise ValueError("PINNED_EPOCH_AND_PUBLIC_KEY_REQUIRED")
        self.epoch = expected_epoch
        self.verifier = Ed25519PublicKey.from_public_bytes(verifier_public_key)
        self.dsn = checked_disposable_dsn()
        if self.inspect() is None:
            raise RuntimeError("AUTHORITY_NOT_VALIDATED")

    def _lock_and_validate(self, db):
        row = db.execute(
            "SELECT epoch,schema,capacity FROM research_trace_meta WHERE singleton=true FOR UPDATE"
        ).fetchone()
        if not row or row[0] != self.epoch or row[1] != SCHEMA or not 1 <= row[2] <= 16:
            raise RuntimeError("AUTHORITY_EPOCH_OR_SCHEMA_UNPROVEN")
        return row[2]

    def inspect(self):
        try:
            with _open(self.dsn) as db:
                with db.transaction():
                    self._lock_and_validate(db)
                    return db.execute("SELECT count(*) FROM research_trace_slots").fetchone()[0]
        except Exception:
            return None

    def acquire(self, query_ref):
        if not isinstance(query_ref,str) or not QUERY_REF_RE.fullmatch(query_ref):
            return Admission(None,None,"invalid-reference")
        token = uuid.uuid4().hex
        try:
            with _open(self.dsn) as db:
                with db.transaction():
                    cap = self._lock_and_validate(db)
                    occupancy = db.execute("SELECT count(*) FROM research_trace_slots").fetchone()[0]
                    if occupancy >= cap:
                        return Admission(None,occupancy,"full")
                    if db.execute("SELECT 1 FROM research_trace_slots WHERE query_ref=%s",
                                  (query_ref,)).fetchone():
                        return Admission(None,occupancy,"duplicate")
                    db.execute(
                        "INSERT INTO research_trace_slots(token,epoch,query_ref) VALUES (%s,%s,%s)",
                        (token,self.epoch,query_ref))
            return Admission(token,occupancy+1,"admitted")
        except Exception:
            return Admission(None,None,"unavailable")

    def release_witnessed(self, receipt, signature):
        fields = {"version","epoch","token","query_ref","evidence_id","verdict"}
        if (type(receipt) is not dict or set(receipt)!=fields
            or type(signature) is not bytes or len(signature)!=64
            or receipt["version"]!=RECEIPT_VERSION
            or receipt["epoch"]!=self.epoch
            or receipt["verdict"]!=CLOSED
            or not isinstance(receipt["token"],str)
            or not re.fullmatch(r"[0-9a-f]{32}",receipt["token"])
            or not isinstance(receipt["query_ref"],str)
            or not QUERY_REF_RE.fullmatch(receipt["query_ref"])
            or not isinstance(receipt["evidence_id"],str)
            or not QUERY_REF_RE.fullmatch(receipt["evidence_id"])):
            return False
        try:
            self.verifier.verify(signature,_canonical(receipt))
            with _open(self.dsn) as db:
                with db.transaction():
                    self._lock_and_validate(db)
                    count = db.execute(
                        "DELETE FROM research_trace_slots WHERE token=%s AND epoch=%s AND query_ref=%s",
                        (receipt["token"],self.epoch,receipt["query_ref"])).rowcount
                    if count != 1:
                        raise RuntimeError("SLOT_NOT_FOUND_OR_REPLAY")
            return True
        except Exception:
            return False
