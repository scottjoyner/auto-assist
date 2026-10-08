#!/usr/bin/env python3
"""Read-only across-home OpenCode token census and orphaned-agent projection.

Never modifies SQLite or launch ledgers, and never reads prompt or message text.
A missing 'stop' finish or missing final manifest is not a successful run.
"""
from __future__ import annotations
import argparse
import collections
import json
import os
from pathlib import Path
import pwd
import sqlite3
import time

ACCOUNT = Path(pwd.getpwuid(os.getuid()).pw_dir)
DBS = (
    ACCOUNT / ".local/share/opencode/opencode.db",
    ACCOUNT / ".local/share/mcp/dc-isolated/.home/.local/share/opencode/opencode.db",
)
LEDGERS = (
    Path("/home/scott/git/.trace-harvest-20261007-highroi/state/live.jsonl"),
    Path("/home/scott/git/.trace-harvest-20261007-batch2/state/live.jsonl"),
    Path("/home/scott/git/.trace-harvest-20261007-batch3/state/live.jsonl"),
    Path("/home/scott/git/.trace-harvest-20261007-guarded/state/live.jsonl"),
)
TOKENS = ("input", "output", "reasoning", "cache_read", "cache_write")


def read_db(db: Path, *, since_ms: int, cap: int = 5000) -> dict:
    record = {"path": str(db), "found": db.is_file(), "complete": False,
              "sessions": [], "error": None}
    if not db.is_file():
        return record
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        try:
            con.execute("PRAGMA query_only=ON")
            rows = con.execute(
                """SELECT id,title,model,cost,
                    tokens_input,tokens_output,tokens_reasoning,
                    tokens_cache_read,tokens_cache_write,time_updated
                    FROM session WHERE time_updated >= ?
                    ORDER BY time_updated DESC LIMIT ?""", (since_ms, cap+1)).fetchall()
            record["complete"] = len(rows) <= cap
            for (sid,title,raw,cost,*rest) in rows[:cap]:
                try:
                    model = json.loads(raw or "{}")
                except (ValueError, TypeError):
                    model = {}
                if not isinstance(model, dict):
                    model = {}
                values = rest[:5]
                record["sessions"].append({
                    "session_id": sid, "title": title,
                    "provider": model.get("providerID") or "unknown",
                    "model": model.get("id") or model.get("modelID"),
                    "cost": cost, "tokens": dict(zip(TOKENS, values)),
                    "time_updated": rest[-1],
                })
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        record["error"] = type(exc).__name__
    return record


def is_live_agent(pid: object) -> bool:
    if type(pid) is not int or pid < 2:
        return False
    try:
        command = (Path("/proc") / str(pid) / "cmdline").read_bytes().lower()
        return b"opencode" in command and b"run" in command
    except OSError:
        return False


def ledger_projection(paths: tuple[Path, ...], session_ids: set[str]) -> list[dict]:
    records = []
    for path in paths:
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            try:
                r = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(r, dict):
                continue
            sid = r.get("session") or r.get("session_id")
            alive = is_live_agent(r.get("opencode_pid"))
            status = r.get("status")
            records.append({
                "path": str(path), "line": lineno, "session_id": sid,
                "title": r.get("title"), "status": status,
                "process_live": alive,
                "database_found": sid in session_ids if sid else False,
                "orphaned": status == "running" and not alive,
                "outcome": "incomplete" if status == "running" and not alive else status,
            })
    return records


def snapshot(databases=DBS, ledgers=LEDGERS, *, days: int = 2,
             now: float | None = None) -> dict:
    now = time.time() if now is None else now
    since_ms = int((now - days * 86400) * 1000)
    sources = [read_db(Path(p), since_ms=since_ms) for p in databases]
    by_id: dict[str, dict] = {}
    duplicates = []
    by_provider = collections.defaultdict(lambda: {
        "sessions": 0, "tokens": {k: 0 for k in TOKENS}, "cost": 0.0})
    for src in sources:
        for row in src["sessions"]:
            sid = row["session_id"]
            if sid in by_id:
                duplicates.append(sid)
                continue
            by_id[sid] = row
            bucket = by_provider[row["provider"]]
            bucket["sessions"] += 1
            for key in TOKENS:
                token = row["tokens"].get(key)
                if type(token) is int and token > 0:
                    bucket["tokens"][key] += token
            if isinstance(row["cost"], (float,int)) and row["cost"] > 0:
                bucket["cost"] += row["cost"]
    led = ledger_projection(tuple(map(Path,ledgers)), set(by_id))
    return {"schema": "fleet-free-usage-snapshot/v1", "days": days,
            "databases": [{k:v for k,v in d.items() if k != "sessions"} | {"record_count": len(d["sessions"])}
                          for d in sources], "unique_sessions": len(by_id),
            "duplicate_session_ids": sorted(set(duplicates)),
            "by_provider": dict(sorted(by_provider.items())),
            "ledgers": led, "orphan_count": sum(r["orphaned"] for r in led),
            "complete": all(d["complete"] and not d["error"] for d in sources)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=2)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    result = snapshot(days=args.days)
    serialized = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized)
        os.chmod(args.out, 0o600)
    else:
        print(serialized)
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
