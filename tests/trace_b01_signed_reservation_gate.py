"""Standalone synthetic signed reservation gate; NEVER production authority.

Two independent Ed25519 signatures are independently checked against pinned
fixture roots BEFORE reserving replay state. A durable SQLite journal plus a
separately located, fsynced anchor fail closed after *DB-only* rollback.
This does not atomically commit Neo4j and SQLite, prevent co-rollback, or
constrain an arbitrary-Cypher writer. Pending reservations never auto-expire.
"""
from __future__ import annotations
from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SCHEMA = "assistx-b01-dual-signed-fixture-reservation-v0"
_ID = re.compile(r"fixture-(?:task|agent)-[A-Za-z0-9_-]{4,48}")
_NONCE = re.compile(r"nonce-[A-Za-z0-9_-]{6,48}")
_HEX = re.compile(r"[0-9a-f]{64}")
FIELDS = frozenset(("schema","instance","scope","operation","task_id",
    "agent_id","term","sequence","nonce","policy_sha256",
    "issued_at","expires_at"))
class AdmissionDenied(ValueError):
    pass

@dataclass(frozen=True)
class SignedEnvelope:
    body: dict
    issuer_signature: bytes
    device_signature: bytes

def canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",",":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise AdmissionDenied("NONCANONICAL_EVIDENCE") from None

def proof_digest(envelope: SignedEnvelope) -> str:
    return hashlib.sha256(canonical(envelope.body)
        + envelope.issuer_signature + envelope.device_signature).hexdigest()

def verify_envelope(envelope: SignedEnvelope, issuer_root: bytes,
                    device_root: bytes, *, now: int) -> str:
    if (type(envelope) is not SignedEnvelope or
            type(envelope.body) is not dict or
            set(envelope.body) != FIELDS or
            type(issuer_root) is not bytes or len(issuer_root)!=32 or
            type(device_root) is not bytes or len(device_root)!=32 or
            issuer_root == device_root or
            type(envelope.issuer_signature) is not bytes or
            len(envelope.issuer_signature)!=64 or
            type(envelope.device_signature) is not bytes or
            len(envelope.device_signature)!=64):
        raise AdmissionDenied("MALFORMED_OR_UNPINNED_PROOF")
    body=envelope.body
    if (body["schema"]!=SCHEMA
            or body["scope"]!="fixture-assistx-b01"
            or body["operation"]!="claim_fixture_task"
            or type(body["instance"]) is not str
            or _HEX.fullmatch(body["instance"]) is None
            or any(type(body[k]) is not str
                   or _ID.fullmatch(body[k]) is None
                   for k in ("task_id","agent_id"))
            or type(body["nonce"]) is not str
            or _NONCE.fullmatch(body["nonce"]) is None
            or type(body["policy_sha256"]) is not str
            or _HEX.fullmatch(body["policy_sha256"]) is None
            or type(body["term"]) is not int or body["term"]<1
            or type(body["sequence"]) is not int or body["sequence"]<1
            or type(body["issued_at"]) is not int
            or type(body["expires_at"]) is not int
            or type(now) is not int
            or not (body["issued_at"]<=now<body["expires_at"])
            or body["expires_at"]-body["issued_at"]>60):
        raise AdmissionDenied("UNTRUSTED_PROOF_FIELDS_OR_EXPIRY")
    msg=canonical(body)
    try:
        Ed25519PublicKey.from_public_bytes(issuer_root).verify(
            envelope.issuer_signature,msg)
        Ed25519PublicKey.from_public_bytes(device_root).verify(
            envelope.device_signature,msg)
    except (InvalidSignature,ValueError):
        raise AdmissionDenied("INVALID_PINNED_DUAL_SIGNATURE") from None
    return hashlib.sha256(msg + envelope.issuer_signature
                          + envelope.device_signature).hexdigest()

def _connect(path: Path):
    conn=sqlite3.connect(path,timeout=6,isolation_level=None)
    conn.execute("PRAGMA busy_timeout=6000")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA journal_mode=DELETE")
    return conn

def _atomic_anchor(path: Path, state: dict):
    temp=path.with_name(path.name+"."+secrets.token_hex(7)+".pending")
    try:
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"wb") as f:
            f.write(canonical(state))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)

def _read_anchor(path: Path):
    try:
        return json.loads(path.read_bytes())
    except (OSError,ValueError,TypeError):
        raise AdmissionDenied("ANCHOR_UNAVAILABLE_OR_INVALID") from None
class SignedResourceGate:
    """Fixture-only single-owner ledger; no released/writer capability."""

    def __init__(self, db: Path, anchor: Path,
                 issuer_root: bytes, device_root: bytes):
        self.db=Path(db)
        self.anchor=Path(anchor)
        if (self.db.resolve()==self.anchor.resolve()
                or self.db.parent.resolve()==self.anchor.parent.resolve()):
            raise AdmissionDenied("INDEPENDENT_ANCHOR_DIRECTORY_REQUIRED")
        if (type(issuer_root) is not bytes or type(device_root) is not bytes
                or len(issuer_root)!=32 or len(device_root)!=32
                or issuer_root==device_root):
            raise AdmissionDenied("INVALID_DISTINCT_TRUST_ROOTS")
        self.issuer_root=issuer_root
        self.device_root=device_root
        self.snapshot()

    @classmethod
    def bootstrap(cls, db: Path, anchor: Path, issuer_root: bytes,
                  device_root: bytes, task_id: str, agent_id: str,
                  policy_sha256: str):
        db,anchor=Path(db),Path(anchor)
        if db.exists() or anchor.exists():
            raise AdmissionDenied("BOOTSTRAP_MUST_BE_FRESH")
        if (not _ID.fullmatch(task_id) or not _ID.fullmatch(agent_id)
                or not _HEX.fullmatch(policy_sha256)):
            raise AdmissionDenied("INVALID_FIXTURE_BINDING")
        state={
            "schema":SCHEMA,"instance":secrets.token_hex(32),
            "scope":"fixture-assistx-b01","term":1,"high_water":0,
            "task_id":task_id,"agent_id":agent_id,
            "policy_sha256":policy_sha256,
            "issuer_root":issuer_root.hex(),"device_root":device_root.hex(),
            "used_nonces":[],"pending":None,
        }
        with closing(_connect(db)) as conn, conn:
            conn.execute("CREATE TABLE state(id INTEGER PRIMARY KEY,"
                         "document TEXT NOT NULL)")
            conn.execute("INSERT INTO state VALUES(1,?)",
                         (canonical(state).decode("ascii"),))
        _atomic_anchor(anchor,state)
        return cls(db,anchor,issuer_root,device_root)

    def _locked_state(self,conn):
        row=conn.execute("SELECT document FROM state WHERE id=1").fetchone()
        if not row:
            raise AdmissionDenied("RESOURCE_AUTHORITY_MISSING")
        try:
            state=json.loads(row[0])
        except (ValueError,TypeError):
            raise AdmissionDenied("RESOURCE_STATE_CORRUPT") from None
        if canonical(state)!=canonical(_read_anchor(self.anchor)):
            raise AdmissionDenied("ROLLBACK_OR_UNCERTAIN_COMMIT_BLOCKED")
        if (state.get("issuer_root")!=self.issuer_root.hex()
                or state.get("device_root")!=self.device_root.hex()
                or state.get("schema")!=SCHEMA):
            raise AdmissionDenied("PINNED_ROOT_OR_SCHEMA_MISMATCH")
        return state
    def snapshot(self):
        if not self.db.is_file() or not self.anchor.is_file():
            raise AdmissionDenied("RESOURCE_AUTHORITY_OR_ANCHOR_MISSING")
        with closing(_connect(self.db)) as conn, conn:
            return self._locked_state(conn)

    def reserve(self,envelope: SignedEnvelope,*,now: int|None=None):
        # Clock is injectable only for deterministic synthetic tests.
        when=int(time.time()) if now is None else now
        # Freeze caller-owned mutable dict BEFORE verifying signatures; never
        # evaluate identity fields from a different post-verification object.
        if type(envelope) is not SignedEnvelope:
            raise AdmissionDenied("SIGNED_ENVELOPE_REQUIRED")
        frozen=SignedEnvelope(json.loads(canonical(envelope.body)),
                              envelope.issuer_signature,
                              envelope.device_signature)
        digest=verify_envelope(frozen,self.issuer_root,
                               self.device_root,now=when)
        with closing(_connect(self.db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            state=self._locked_state(conn)
            b=frozen.body
            if (b["instance"]!=state["instance"]
                    or b["scope"]!=state["scope"]
                    or b["task_id"]!=state["task_id"]
                    or b["agent_id"]!=state["agent_id"]
                    or b["policy_sha256"]!=state["policy_sha256"]):
                raise AdmissionDenied("UNAUTHORIZED_FIXTURE_BINDING")
            if state["pending"] is not None:
                raise AdmissionDenied("UNCERTAIN_INFLIGHT_CLAIM_BLOCKED")
            if b["term"]!=state["term"]:
                raise AdmissionDenied("STALE_OR_FORKED_TERM")
            if b["sequence"]!=state["high_water"]+1:
                raise AdmissionDenied("STALE_OR_FORKED_SEQUENCE")
            if b["nonce"] in state["used_nonces"]:
                raise AdmissionDenied("REPLAYED_NONCE")
            new_state={**state,
                "high_water":b["sequence"],
                "used_nonces":[*state["used_nonces"],b["nonce"]],
                "pending":{"digest":digest,"nonce":b["nonce"],
                           "term":b["term"],"sequence":b["sequence"]},
            }
            # Anchor first: a crash before DB COMMIT fails closed on re-open.
            # Cross-file atomic commit is NOT claimed.
            _atomic_anchor(self.anchor,new_state)
            conn.execute("UPDATE state SET document=? WHERE id=1",
                         (canonical(new_state).decode("ascii"),))
            conn.execute("COMMIT")
        return {"schema":"assistx-b01-durable-pending-ticket-v0",
                "proof_sha256":digest,"term":b["term"],
                "sequence":b["sequence"],"state":"PENDING_UNCERTAIN",
                "graph_write_authorized":False,
                "production_authority":False}

    def read_only_reconcile(self):
        s=self.snapshot()
        return {"state":"PENDING_UNCERTAIN" if s["pending"] else "IDLE",
                "high_water":s["high_water"],"term":s["term"],
                "automatic_reexecution_permitted":False,
                "automatic_failover_permitted":False}