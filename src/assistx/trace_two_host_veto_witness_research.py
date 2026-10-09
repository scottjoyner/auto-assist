"""Two-host veto witness for #148 research; NOT distributed consensus.

The authoritative host x1-370 first reserves an occupied slot in its
disposable SingleAuthorityResearch journal. A *separate* xwing witness then
records one nonexpiring exact reservation and signs a witness endorsement.
Only a verifier with a PUBLIC KEY PINNED *before* the request may validate
the endorsement. If xwing is missing, x1 may keep its reserved slot but a
research client must not report a quorum-accepted proposal.

This module NEVER authorizes Neo4j execution, physical slot release,
production API calls, or failover promotion. No lease expiry, rearming,
automatic recovery, or mutable routing. Two copied witness stores (or copied
private signer keys) still admit split-brain: quorum/consensus is NOT proven.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid
from urllib.parse import quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey

SCHEMA="assistx-two-node-epoch-veto-witness-research-v1"
DB_NAME="witness-test.sqlite"
KEY_NAME="witness-test.key"
PUB_NAME="witness-test.pub"
HEX64=re.compile(r"^[a-f0-9]{64}$")
HEX32=re.compile(r"^[a-f0-9]{32}$")
QUERY=re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


def _uuid4(value):
    try:
        return type(value) is str and str(uuid.UUID(value))==value and uuid.UUID(value).version==4
    except (TypeError,ValueError,AttributeError):
        return False


def _dir(path):
    p=Path(path)
    if (not p.is_absolute() or not p.name.startswith("assistx-twohost-witness-test-")
        or p.parent != Path("/tmp") or p.is_symlink()):
        raise ValueError("ONLY_DISPOSABLE_WITNESS_DIRECTORY")
    return p


def _connect(db):
    conn=sqlite3.connect("file:"+quote(str(db),safe="/")+"?mode=rw",uri=True,
                         isolation_level=None,timeout=1.0)
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def _read_0600(path):
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_mode & 0o077:
        raise ValueError("UNSAFE_WITNESS_FILE")
    return path.read_bytes(),(st.st_dev,st.st_ino)


def bootstrap_disposable_witness(folder,epoch,graph_id):
    root=_dir(folder)
    if not _uuid4(epoch) or type(graph_id) is not str or not HEX64.fullmatch(graph_id):
        raise ValueError("INVALID_PINNED_WITNESS_IDENTITY")
    if root.exists():
        raise ValueError("WITNESS_FIXTURE_ALREADY_EXISTS")
    root.mkdir(mode=0o700)
    private=Ed25519PrivateKey.generate()
    raw=private.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,
                              serialization.NoEncryption())
    public=private.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw)
    for name,bytes_value in ((KEY_NAME,raw),(PUB_NAME,public),(DB_NAME,b"")):
        fd=os.open(root/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"wb") as file:
            file.write(bytes_value)
            file.flush()
            os.fsync(file.fileno())
    digest=hashlib.sha256(public).hexdigest()
    with _connect(root/DB_NAME) as c:
        c.executescript("""
          CREATE TABLE owner (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            schema_version TEXT NOT NULL,
            epoch TEXT NOT NULL, graph_id TEXT NOT NULL,
            signer_sha256 TEXT NOT NULL, highwater INTEGER NOT NULL);
          CREATE TABLE witness_grant (
            sequence INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE,
            receiver_nonce TEXT NOT NULL UNIQUE, query_ref TEXT NOT NULL UNIQUE);
        """)
        c.execute("INSERT INTO owner VALUES(1,?,?,?,?,0)",
                  (SCHEMA,epoch,graph_id,digest))
    return {"signer_sha256":digest,"public_key":public,"epoch":epoch,
            "graph_id":graph_id}


def _canonical(envelope):
    return json.dumps(envelope,sort_keys=True,separators=(",",":")).encode("utf-8")


class VetoWitnessResearch:
    """Research-only one-grant veto; signing key never reaches primary host."""

    def __init__(self,folder,*,pinned_epoch,pinned_graph_id,pinned_signer_digest,
                 independently_pinned_minimum_sequence=0):
        self.root=_dir(folder)
        self.db=self.root/DB_NAME
        self.key=self.root/KEY_NAME
        self.pub=self.root/PUB_NAME
        if (not _uuid4(pinned_epoch) or type(pinned_graph_id) is not str
            or not HEX64.fullmatch(pinned_graph_id)
            or type(pinned_signer_digest) is not str
            or not HEX64.fullmatch(pinned_signer_digest)
            or type(independently_pinned_minimum_sequence) is not int
            or independently_pinned_minimum_sequence<0):
            raise ValueError("EXTERNALLY_PINNED_WITNESS_IDENTITY_REQUIRED")
        self.epoch=pinned_epoch
        self.graph=pinned_graph_id
        self.digest=pinned_signer_digest
        self.floor=independently_pinned_minimum_sequence
        key,self._key_identity=_read_0600(self.key)
        pub,self._pub_identity=_read_0600(self.pub)
        db,self._db_identity=_read_0600(self.db)
        if len(key)!=32 or len(pub)!=32 or hashlib.sha256(pub).hexdigest()!=self.digest:
            raise ValueError("RESEARCH_KEY_DIGEST_NOT_PINNED")
        if Ed25519PrivateKey.from_private_bytes(key).public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)!=pub:
            raise ValueError("RESEARCH_SIGNER_KEY_MISMATCH")
        with _connect(self.db) as c:
            self._validate(c)

    def _validate(self,conn):
        if (_read_0600(self.db)[1]!=self._db_identity
            or _read_0600(self.key)[1]!=self._key_identity
            or _read_0600(self.pub)[1]!=self._pub_identity):
            raise ValueError("WITNESS_STORE_OR_KEY_REPLACED")
        row=conn.execute("SELECT schema_version,epoch,graph_id,signer_sha256,highwater "
                         "FROM owner WHERE singleton=1").fetchone()
        if (not row or row[:4]!=(SCHEMA,self.epoch,self.graph,self.digest)
            or type(row[4]) is not int or row[4]<self.floor
            or conn.execute("SELECT count(*) FROM owner").fetchone()[0]!=1):
            raise ValueError("WITNESS_EPOCH_GRAPH_SIGNER_OR_CHECKPOINT_MISMATCH")
        return row[4]

    def inspect(self):
        with _connect(self.db) as c:
            seq=self._validate(c)
            occupied=c.execute("SELECT count(*) FROM witness_grant").fetchone()[0]
            return {"highwater":seq,"occupied":occupied,"signer_sha256":self.digest}

    def endorse(self,*,token,receiver_nonce,query_ref,sequence):
        if (type(token) is not str or not HEX32.fullmatch(token)
            or not _uuid4(receiver_nonce) or type(query_ref) is not str
            or not QUERY.fullmatch(query_ref)
            or type(sequence) is not int or sequence<1):
            return {"status":"invalid-prepared-admission"}
        try:
            with _connect(self.db) as conn:
                conn.execute("BEGIN IMMEDIATE")
                previous=self._validate(conn)
                if sequence!=previous+1:
                    conn.rollback()
                    return {"status":"stale-or-out-of-order"}
                if conn.execute("SELECT count(*) FROM witness_grant").fetchone()[0]>=1:
                    conn.rollback()
                    return {"status":"witness-capacity-full"}
                conn.execute("INSERT INTO witness_grant VALUES(?,?,?,?)",
                             (sequence,token,receiver_nonce,query_ref))
                conn.execute("UPDATE owner SET highwater=? WHERE singleton=1",
                             (sequence,))
                conn.commit()
                self.floor=max(self.floor,sequence)
                # The grant is durable before the signer is loaded. A process
                # crash here leaves a held slot, not an optimistic rearm.
            raw,_=_read_0600(self.key)
            envelope={
                "schema":SCHEMA,"epoch":self.epoch,"graph_id":self.graph,
                "sequence":sequence,"token":token,
                "receiver_nonce":receiver_nonce,"query_ref":query_ref,
                "status":"witnessed-not-executable",
            }
            sig=Ed25519PrivateKey.from_private_bytes(raw).sign(_canonical(envelope))
            return {"status":"witnessed-not-executable","envelope":envelope,
                    "signature_hex":sig.hex()}
        except (ValueError,OSError,sqlite3.Error):
            return {"status":"witness-unavailable"}


def verify_pretrusted_endorsement(answer,*,public_key,approved_sha256,
                                  epoch,graph_id,token,receiver_nonce,
                                  query_ref,sequence):
    """A signed witness record is not a Neo4j execution/admission grant."""
    try:
        if (type(public_key) is not bytes or len(public_key)!=32
            or hashlib.sha256(public_key).hexdigest()!=approved_sha256
            or type(answer) is not dict or set(answer)!={"status","envelope","signature_hex"}
            or answer["status"]!="witnessed-not-executable"):
            return False
        payload=answer["envelope"]
        if (type(payload) is not dict or payload!={
            "schema":SCHEMA,"epoch":epoch,"graph_id":graph_id,
            "sequence":sequence,"token":token,
            "receiver_nonce":receiver_nonce,"query_ref":query_ref,
            "status":"witnessed-not-executable",
        }):
            return False
        signature=bytes.fromhex(answer["signature_hex"])
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature,_canonical(payload))
        return True
    except (ValueError,TypeError,KeyError,InvalidSignature):
        return False
