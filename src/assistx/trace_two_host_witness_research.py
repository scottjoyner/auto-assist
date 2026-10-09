"""Offline, research-only second-host witness for a *single* admission slot.

A witness on xwing durably refuses additional reservations while any original
reservation remains. Its private Ed25519 key lives only in a disposable 0600
test directory on that machine. A client on x1-370 must pin the public key
separately and verify each returned grant.

LIMITS: no production import, no release/lease expiry, no worker fencing,
no quorum, no globally durable signer trust, no safe owner promotion.
RESTORING the witness from an earlier snapshot or cloning it enables
split brain; a signature by itself cannot prevent that.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

SCHEMA = "assistx-two-host-witness-research-v1"
DB_NAME = "witness-test.sqlite"
KEY_NAME = "witness-test.key"
PUB_NAME = "witness-test.pub"


def guarded_dir(directory: str) -> Path:
    root=Path(directory)
    if (not root.is_absolute() or root.parent!=Path("/tmp")
        or not root.name.startswith("assistx-twohost-witness-test-")
        or root.is_symlink()):
        raise ValueError("DISPOSABLE_WITNESS_PATH_ONLY")
    return root


def _file_identity(path:Path)->tuple[int,int]:
    if path.is_symlink():
        raise ValueError("WITNESS_SYMLINK_REJECTED")
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_mode & 0o077:
        raise ValueError("WITNESS_FILE_INSECURE")
    return st.st_dev,st.st_ino


def _open_db(path:Path):
    db=sqlite3.connect("file:"+quote(str(path),safe="/")+"?mode=rw",
                       uri=True,isolation_level=None,timeout=1.5)
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA busy_timeout=1500")
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def _uuid4(v):
    try:
        return type(v) is str and str(uuid.UUID(v))==v and uuid.UUID(v).version==4
    except (ValueError,TypeError,AttributeError):
        return False


def _validate_identities(epoch:str,graph:str):
    if (not _uuid4(epoch) or type(graph) is not str or len(graph)!=64
        or any(c not in "0123456789abcdef" for c in graph)):
        raise ValueError("INVALID_PINNED_EPOCH_GRAPH")


def _grant_bytes(grant:dict)->bytes:
    if type(grant) is not dict or set(grant)!={
        "schema","epoch","graph_id","query_ref","receiver_nonce",
        "reservation_token","sequence"}:
        raise ValueError("MALFORMED_WITNESS_GRANT")
    _validate_identities(grant["epoch"],grant["graph_id"])
    if (grant["schema"]!=SCHEMA or type(grant["query_ref"]) is not str
        or not 1<=len(grant["query_ref"])<=128
        or any(not (c.isascii() and (c.isalnum() or c in "-_:.")) for c in grant["query_ref"])
        or not _uuid4(grant["receiver_nonce"])
        or type(grant["reservation_token"]) is not str
        or len(grant["reservation_token"])!=32
        or any(c not in "0123456789abcdef" for c in grant["reservation_token"])
        or type(grant["sequence"]) is not int or grant["sequence"]<1):
        raise ValueError("INVALID_GRANT_FIELDS")
    return json.dumps(grant,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")


def bootstrap_disposable_witness(directory:str,*,epoch:str,graph_id:str):
    root=guarded_dir(directory)
    _validate_identities(epoch,graph_id)
    if root.exists():
        raise ValueError("RESEARCH_WITNESS_EXISTS_NO_REARM")
    root.mkdir(mode=0o700)
    key=Ed25519PrivateKey.generate()
    raw=key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,
                          serialization.NoEncryption())
    pub=key.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    try:
        for name, data in ((KEY_NAME,raw),(PUB_NAME,pub)):
            fd=os.open(root/name,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
            with os.fdopen(fd,"wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        path=root/DB_NAME
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_RDWR,0o600)
        os.close(fd)
        with _open_db(path) as db:
            db.executescript("""
              CREATE TABLE authority (
                id INTEGER PRIMARY KEY CHECK(id=1),
                epoch TEXT NOT NULL, graph_id TEXT NOT NULL,
                signer_digest TEXT NOT NULL, sequence INTEGER NOT NULL);
              CREATE TABLE occupied (
                reservation_token TEXT PRIMARY KEY,
                receiver_nonce TEXT NOT NULL UNIQUE,
                query_ref TEXT NOT NULL UNIQUE,
                sequence INTEGER NOT NULL UNIQUE);
            """)
            db.execute("INSERT INTO authority VALUES(1,?,?,?,0)",
                       (epoch,graph_id,hashlib.sha256(pub).hexdigest()))
    except Exception:
        # Remain fail-closed with any partial files: never auto-repair.
        raise
    return pub.hex()


class Witness:
    def __init__(self,directory:str,*,epoch:str,graph_id:str,expected_public_key:bytes,
                 minimum_sequence:int=0):
        root=guarded_dir(directory)
        _validate_identities(epoch,graph_id)
        if (type(expected_public_key) is not bytes or len(expected_public_key)!=32
            or type(minimum_sequence) is not int or minimum_sequence<0):
            raise ValueError("WITNESS_TRUST_PIN_REQUIRED")
        self.root=root
        self.path=root/DB_NAME
        self.epoch=epoch
        self.graph=graph_id
        self.pub=expected_public_key
        self.digest=hashlib.sha256(expected_public_key).hexdigest()
        self.floor=minimum_sequence
        self.file_id=_file_identity(self.path)
        self.key_id=_file_identity(root/KEY_NAME)
        self.pub_id=_file_identity(root/PUB_NAME)
        with _open_db(self.path) as db:
            self._verify(db)

    def _verify(self,db)->int:
        if (self.file_id!=_file_identity(self.path)
            or self.key_id!=_file_identity(self.root/KEY_NAME)
            or self.pub_id!=_file_identity(self.root/PUB_NAME)
            or (self.root/PUB_NAME).read_bytes()!=self.pub):
            raise ValueError("WITNESS_INODE_OR_TRUST_CHANGED")
        row=db.execute("SELECT epoch,graph_id,signer_digest,sequence FROM authority "
                       "WHERE id=1").fetchone()
        if (row is None or row[:3]!=(self.epoch,self.graph,self.digest)
            or type(row[3]) is not int or row[3]<self.floor
            or db.execute("SELECT count(*) FROM authority").fetchone()[0]!=1):
            raise ValueError("WITNESS_EPOCH_OR_ROLLBACK_UNTRUSTED")
        return row[3]

    def snapshot(self)->dict:
        with _open_db(self.path) as db:
            seq=self._verify(db)
            count=db.execute("SELECT count(*) FROM occupied").fetchone()[0]
            return {"epoch":self.epoch,"graph_id":self.graph,
                    "sequence":seq,"active":count,"capacity":1}

    def reserve(self,query_ref:str)->dict:
        # No release in research. Crash after commit but before caller receipt
        # intentionally strands the grant rather than risking double use.
        try:
            if _grant_bytes({
                "schema":SCHEMA,"epoch":self.epoch,"graph_id":self.graph,
                "query_ref":query_ref,"receiver_nonce":str(uuid.uuid4()),
                "reservation_token":uuid.uuid4().hex,"sequence":1
            }) is None:
                raise ValueError("UNREACHABLE")
        except (ValueError,TypeError):
            return {"status":"invalid-query-ref"}
        try:
            with _open_db(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                sequence=self._verify(db)
                if db.execute("SELECT count(*) FROM occupied").fetchone()[0]>=1:
                    db.rollback()
                    return {"status":"full"}
                grant={
                    "schema":SCHEMA,"epoch":self.epoch,"graph_id":self.graph,
                    "query_ref":query_ref,"receiver_nonce":str(uuid.uuid4()),
                    "reservation_token":uuid.uuid4().hex,"sequence":sequence+1
                }
                db.execute("INSERT INTO occupied VALUES(?,?,?,?)",
                    (grant["reservation_token"],grant["receiver_nonce"],
                     grant["query_ref"],grant["sequence"]))
                db.execute("UPDATE authority SET sequence=? WHERE id=1",(sequence+1,))
                db.commit()
                self.floor=max(self.floor,sequence+1)
            key=Ed25519PrivateKey.from_private_bytes((self.root/KEY_NAME).read_bytes())
            if (key.public_key().public_bytes(
                serialization.Encoding.Raw,serialization.PublicFormat.Raw)!=self.pub):
                return {"status":"witness-key-uncertain"}
            return {"status":"reserved","grant":grant,
                    "signature_hex":key.sign(_grant_bytes(grant)).hex()}
        except (OSError,ValueError,sqlite3.Error):
            return {"status":"witness-unavailable"}



def apply_witness_grant_to_primary(primary, signed_row:dict, trusted_key:bytes,
                                   *, query_ref:str)->dict:
    """Bind primary reservation to the EXACT signed external token and nonce.

    Research transaction only. A caller cannot rely on offline signature alone
    under journal rollback/replay; production would also need a quorum-verified
    live one-time consumption state and an unforgeable admission service.
    """
    if not verify_external_grant(signed_row,trusted_key,epoch=primary.epoch,
                                 graph_id=primary.graph,query_ref=query_ref):
        return {"status":"untrusted-witness-grant"}
    grant=signed_row["grant"]
    try:
        # Import of an underscore helper stays confined to this uninstalled
        # research adapter. No changes to the production graph admission path.
        from .trace_two_host_authority_research import _connect
        with _connect(primary.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            capacity, sequence=primary._validate(conn)
            active=conn.execute("SELECT count(*) FROM occupied").fetchone()[0]
            if active>=capacity:
                conn.rollback()
                return {"status":"full"}
            if sequence+1 != grant["sequence"]:
                conn.rollback()
                return {"status":"witness-sequence-divergent"}
            conn.execute("INSERT INTO occupied VALUES(?,?,?,?)",
                (grant["reservation_token"],query_ref,
                 grant["receiver_nonce"],grant["sequence"]))
            conn.execute("UPDATE owner SET sequence=? WHERE singleton=1",
                         (grant["sequence"],))
            conn.commit()
            primary.floor=max(primary.floor,grant["sequence"])
        if primary._identity()!=primary.identity:
            return {"status":"primary-identity-uncertain"}
        return {"status":"admitted","primary_sequence":grant["sequence"],
                "reservation_token":grant["reservation_token"],
                "receiver_nonce":grant["receiver_nonce"]}
    except (OSError,ValueError,sqlite3.Error):
        return {"status":"primary-unavailable"}


def verify_external_grant(row:dict,pinned_public_key:bytes,*,epoch:str,graph_id:str,
                          query_ref:str)->bool:
    """Independent pinned trust: NEVER read signer from incoming row."""
    try:
        if (type(row) is not dict or row.get("status")!="reserved"
            or type(pinned_public_key) is not bytes or len(pinned_public_key)!=32):
            return False
        grant=row["grant"]
        if grant["epoch"]!=epoch or grant["graph_id"]!=graph_id or grant["query_ref"]!=query_ref:
            return False
        sig=bytes.fromhex(row["signature_hex"])
        if len(sig)!=64:
            return False
        Ed25519PublicKey.from_public_bytes(pinned_public_key).verify(
            sig,_grant_bytes(grant))
        return True
    except (KeyError,ValueError,TypeError,InvalidSignature):
        return False
