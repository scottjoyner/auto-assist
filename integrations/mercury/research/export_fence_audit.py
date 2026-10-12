#!/usr/bin/env python3
"""Export only synthetic decision events after verifying SHA-256 chain."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from fenced_dispatch_probe import compact, inspect

ALLOWED = {"event", "generation", "issuer", "at_ms", "node", "job",
           "claim", "allowed", "reason"}

def export(db: str, output: str) -> dict:
    report = inspect(db)
    con = sqlite3.connect("file:" + str(Path(db).resolve()) + "?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT seq,prev,hash,value FROM audit ORDER BY seq").fetchall()
    finally:
        con.close()
    clean = []
    for seq, prev, digest, raw in rows:
        event = json.loads(raw)
        if not isinstance(event,dict) or set(event) - ALLOWED:
            raise ValueError("non_fixture_field_in_audit")
        clean.append({"seq":seq,"prev":prev,"hash":digest,"event":event})
    content = "".join(json.dumps(e,sort_keys=True,separators=(",",":")) + "\n" for e in clean)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)
    return {"events":len(rows),"source_head":report["head"],
            "sha256":hashlib.sha256(content.encode()).hexdigest(),
            "mock_dispatches":report["mock_dispatches"],"denials":report["denials"]}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",required=True)
    parser.add_argument("--output",required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.db,args.output),sort_keys=True))
