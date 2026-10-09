#!/usr/bin/env python3
"""#148 disposable, SINGLE Raspberry Pi authority shared by x1-370 and xwing.

RESEARCH ONLY. This is one non-failing-owner SQLite witness, NOT a quorum or
multi-node consensus system. No runtime module imports this file. It exposes
no daemon, API, TCP listener, Neo4j, receipt signing, or capacity release.

Call it only via an existing strictly host-key-checked SSH session. A lost
connection or unavailable file is denial, never fallback to a copied journal.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
from urllib.parse import quote
import uuid

HOST="raspberrypi"
NAME="witness-test.sqlite"
SCHEMA="assistx-third-node-witness-v1"


def _safe_path(name: str)->Path:
    p=Path(name)
    if (not p.is_absolute() or p.name!=NAME
        or p.parent.parent!=Path("/tmp")
        or not p.parent.name.startswith("assistx-threehost-witness-test-")
        or p.parent.is_symlink() or p.is_symlink()):
        raise ValueError("RESEARCH_DISPOSABLE_PATH_ONLY")
    return p


def _valid_uuid(value:str)->bool:
    try:
        return type(value) is str and str(uuid.UUID(value))==value and uuid.UUID(value).version==4
    except (ValueError,TypeError,AttributeError):
        return False


def _connection(p:Path):
    c=sqlite3.connect("file:"+quote(str(p),safe="/")+"?mode=rw",
                      uri=True,isolation_level=None,timeout=1.2)
    c.execute("PRAGMA synchronous=FULL")
    c.execute("PRAGMA busy_timeout=1200")
    c.execute("PRAGMA trusted_schema=OFF")
    return c


def bootstrap(name:str,epoch:str,graph:str)->dict:
    p=_safe_path(name)
    if (not _valid_uuid(epoch) or type(graph) is not str
        or len(graph)!=64 or any(ch not in "0123456789abcdef" for ch in graph)
        or p.exists()):
        raise ValueError("RESEARCH_BOOTSTRAP_INVALID")
    p.parent.mkdir(mode=0o700,exist_ok=True)
    if stat.S_IMODE(p.parent.stat().st_mode)!=0o700:
        raise ValueError("UNSAFE_DISPOSABLE_PARENT_MODE")
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600)
    os.close(fd)
    with _connection(p) as c:
        c.executescript("""
          CREATE TABLE owner (
            id INTEGER PRIMARY KEY CHECK(id=1),
            schema TEXT NOT NULL, epoch TEXT NOT NULL,
            graph TEXT NOT NULL, cap INTEGER NOT NULL,
            sequence INTEGER NOT NULL);
          CREATE TABLE active (
            token TEXT PRIMARY KEY, query_ref TEXT UNIQUE NOT NULL,
            receiver_nonce TEXT UNIQUE NOT NULL, sequence INTEGER UNIQUE NOT NULL);
        """)
        c.execute("INSERT INTO owner VALUES(1,?,?,?,?,0)",(SCHEMA,epoch,graph,1))
    return {"status":"bootstrapped","host":HOST}


class Witness:
    def __init__(self,name:str,epoch:str,graph:str):
        self.path=_safe_path(name)
        if not _valid_uuid(epoch) or type(graph) is not str or len(graph)!=64:
            raise ValueError("INVALID_PINNED_IDENTITY")
        self.epoch=epoch
        self.graph=graph
        self.inode=self._identity()
        with _connection(self.path) as c:self._validate(c)

    def _identity(self):
        st=self.path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1
            or (stat.S_IMODE(st.st_mode)&0o077)):
            raise ValueError("UNSAFE_WITNESS_FILE")
        return (st.st_dev,st.st_ino)

    def _validate(self,c):
        if self.inode!=self._identity():
            raise ValueError("WITNESS_REPLACED")
        item=c.execute("SELECT schema,epoch,graph,cap,sequence "
                       "FROM owner WHERE id=1").fetchone()
        if (item is None or item[:4]!=(SCHEMA,self.epoch,self.graph,1)
            or type(item[4]) is not int or item[4]<0
            or c.execute("SELECT count(*) FROM owner").fetchone()[0]!=1):
            raise ValueError("WITNESS_IDENTITY_UNTRUSTED")
        return item[4]

    def request(self,ref:str)->dict:
        if (type(ref) is not str or not ref.startswith("synthetic-")
            or not 11<=len(ref)<=120
            or any(not(c.isascii() and (c.isalnum() or c in "_-:.")) for c in ref)):
            return {"status":"invalid-query-ref","host":HOST}
        try:
            with _connection(self.path) as c:
                c.execute("BEGIN IMMEDIATE")
                sequence=self._validate(c)
                active=c.execute("SELECT count(*) FROM active").fetchone()[0]
                if active:
                    c.rollback()
                    return {"status":"full","host":HOST,"active":active,
                            "sequence":sequence}
                tok=uuid.uuid4().hex
                nonce=str(uuid.uuid4())
                sequence+=1
                c.execute("INSERT INTO active VALUES (?,?,?,?)",
                          (tok,ref,nonce,sequence))
                c.execute("UPDATE owner SET sequence=? WHERE id=1",(sequence,))
                c.commit()
                return {"status":"admitted","host":HOST,"active":1,
                        "sequence":sequence,"token":tok,"receiver_nonce":nonce}
        except (OSError,ValueError,sqlite3.Error):
            return {"status":"authority-unavailable","host":HOST}

    def status(self)->dict:
        with _connection(self.path) as c:
            seq=self._validate(c)
            active=c.execute("SELECT count(*) FROM active").fetchone()[0]
        return {"status":"observed","host":HOST,
                "epoch":self.epoch,"sequence":seq,"active":active,"capacity":1}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("bootstrap","request","status"))
    p.add_argument("--path",required=True)
    p.add_argument("--epoch",required=True)
    p.add_argument("--graph-id",required=True)
    p.add_argument("--query-ref",default="")
    x=p.parse_args()
    if socket.gethostname().split(".")[0]!=HOST:
        raise SystemExit("WRONG_DISPOSABLE_WITNESS_HOST")
    if os.getenv("ASSISTX_THREEHOST_RESEARCH_ONLY")!="yes-disposable":
        raise SystemExit("RESEARCH_OPT_IN_REQUIRED")
    try:
        if x.action=="bootstrap":
            if x.query_ref: raise ValueError("INVALID_BOOTSTRAP_QUERY")
            result=bootstrap(x.path,x.epoch,x.graph_id)
        else:
            w=Witness(x.path,x.epoch,x.graph_id)
            result=w.status() if x.action=="status" else w.request(x.query_ref)
        print(json.dumps(result,sort_keys=True))
    except (OSError,ValueError,sqlite3.Error) as exc:
        print(json.dumps({"status":"authority-unavailable",
                          "host":HOST,"error_type":type(exc).__name__}),
              flush=True)
        raise SystemExit(4)


if __name__=="__main__":main()
