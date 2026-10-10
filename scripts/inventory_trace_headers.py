#!/usr/bin/env python3
"""Read-only, bounded metadata-only trace inventory pilot.

No raw session IDs, message bodies, prompts, coordinates, paths or secrets are output.
Does not choose, create, migrate, or mutate any database.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def inventory(roots: list[Path], max_files: int = 100, max_bytes: int = 8_000_000,
              max_rows: int = 100_000, max_line_bytes: int = 128_000,
              max_dirs: int = 100) -> dict:
    stats = {
        "files_inspected": 0, "files_skipped_oversize": 0,
        "bytes_read": 0, "rows_parsed": 0,
        "rows_malformed": 0, "rows_oversize": 0,
        "with_session_id": 0, "with_parent_session": 0,
        "with_trace_id": 0, "with_span_id": 0,
        "with_node_id": 0, "with_model_metadata": 0,
        "with_token_usage": 0, "with_time_metadata": 0,
    }
    seen_sessions = set()
    start = time.monotonic()
    directories = 0
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for directory, child_dirs, files in os.walk(root, followlinks=False):
            directories += 1
            child_dirs[:] = sorted(d for d in child_dirs
                                   if not (Path(directory) / d).is_symlink())
            if directories > max_dirs:
                child_dirs[:] = []
                break
            for filename in sorted(files):
                if stats["files_inspected"] >= max_files or stats["rows_parsed"] >= max_rows:
                    break
                path = Path(directory) / filename
                if path.suffix != ".jsonl" or path.is_symlink() or not path.is_file():
                    continue
                size = path.stat().st_size
                if size > max_bytes - stats["bytes_read"]:
                    stats["files_skipped_oversize"] += 1
                    continue
                stats["files_inspected"] += 1
                with path.open("rb") as stream:
                    for line in stream:
                        stats["bytes_read"] += len(line)
                        if len(line) > max_line_bytes:
                            stats["rows_oversize"] += 1
                            continue
                        if stats["rows_parsed"] >= max_rows:
                            break
                        try:
                            row = json.loads(line)
                            if not isinstance(row, dict):
                                raise ValueError("not an object")
                        except (ValueError, UnicodeDecodeError):
                            stats["rows_malformed"] += 1
                            continue
                        stats["rows_parsed"] += 1
                        sid = row.get("session_id")
                        if isinstance(sid, str) and sid:
                            stats["with_session_id"] += 1
                            seen_sessions.add(sid)
                        if row.get("parent_session_id"): stats["with_parent_session"] += 1
                        if row.get("trace_id"): stats["with_trace_id"] += 1
                        if row.get("span_id"): stats["with_span_id"] += 1
                        if row.get("node_id") or row.get("node"): stats["with_node_id"] += 1
                        if row.get("model") or row.get("provider"): stats["with_model_metadata"] += 1
                        if isinstance(row.get("tokens"), dict): stats["with_token_usage"] += 1
                        if row.get("time_created") or row.get("timestamp") or row.get("started_at"):
                            stats["with_time_metadata"] += 1
            if stats["files_inspected"] >= max_files or stats["rows_parsed"] >= max_rows:
                break
    stats["unique_sessions_observed"] = len(seen_sessions)
    stats["directories_considered"] = directories
    stats["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
    stats["read_only"] = True
    stats["complete_fleet_coverage"] = False
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--max-files", type=int, default=100)
    parser.add_argument("--max-bytes", type=int, default=8_000_000)
    parser.add_argument("--max-rows", type=int, default=100_000)
    args = parser.parse_args()
    if min(args.max_files, args.max_bytes, args.max_rows) <= 0:
        parser.error("all scan budgets must be positive")
    print(json.dumps(inventory(args.roots, args.max_files, args.max_bytes, args.max_rows),
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
