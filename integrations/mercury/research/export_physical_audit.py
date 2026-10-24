#!/usr/bin/env python3
"""Export only synthetic test events and verify the disposable SQLite hash chain."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from physical_lease_probe import canon

SAFE_FIELDS = {"op", "node", "task", "epoch", "decision", "at_ms",
               "upstreamGroup", "ttl_ms"}
SAFE_OPS = {"boot", "acquire", "renew", "release"}


def export(db, output):
    conn = sqlite3.connect("file:" + str(Path(db).resolve()) + "?mode=ro",
                           uri=True)
    rows = conn.execute(
        "SELECT seq,prev_hash,hash,event_json FROM audit ORDER BY seq"
    ).fetchall()
    prev = "0" * 64
    clean = []
    for seq, old, digest, serialized in rows:
        event = json.loads(serialized)
        if old != prev or hashlib.sha256(prev.encode() + canon(event)).hexdigest() != digest:
            raise ValueError("invalid_chain_event_" + str(seq))
        if not isinstance(event, dict) or set(event) - SAFE_FIELDS or event.get("op") not in SAFE_OPS:
            raise ValueError("unexpected_non_fixture_event")
        clean.append({"sequence": seq, "prev_hash": old,
                      "hash": digest, "event": event})
        prev = digest
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                      for row in clean)
    path.write_text(content)
    print(json.dumps({"verified": True, "events": len(rows), "head": prev,
                      "sha256": hashlib.sha256(content.encode()).hexdigest()}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    export(a.db, a.output)
