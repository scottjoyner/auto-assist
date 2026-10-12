#!/usr/bin/env python3
"""Verify and export only synthetic in-flight gate decisions, never secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from inflight_gate import canon, inspect

ALLOWED = {"event","node","job","claim","generation","issuer",
           "reason","atMs","quarantinedActive","runId","witnessNonce"}

def export(db, output):
    report=inspect(db)
    con=sqlite3.connect("file:"+str(Path(db).resolve())+"?mode=ro",uri=True)
    try:
        rows=con.execute("SELECT seq,prev,hash,event FROM audit ORDER BY seq").fetchall()
    finally:con.close()
    clean=[]
    for seq,prev,digest,raw in rows:
        event=json.loads(raw)
        if not isinstance(event,dict) or set(event)-ALLOWED:
            raise ValueError("unexpected_non_fixture_audit_field")
        clean.append({"seq":seq,"prev":prev,"hash":digest,"event":event})
    text="".join(json.dumps(e,sort_keys=True,separators=(",",":"))+"\n" for e in clean)
    p=Path(output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    return {"events":len(rows),"head":report["head"],
            "sha256":hashlib.sha256(text.encode()).hexdigest(),
            "slot":report["slot"],"issuer":report["issuer"],
            "generation":report["generation"]}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True);p.add_argument("--output",required=True)
    a=p.parse_args()
    print(json.dumps(export(a.db,a.output),sort_keys=True))
