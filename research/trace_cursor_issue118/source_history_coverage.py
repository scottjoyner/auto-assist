"""Research-only, metadata-only source history coverage analyzer.

Legacy size_at_open fields are NOT committed cursor or historic custody proof.
No source files, tar archives, or SQLite payloads are opened by this module.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import stat
import sys

MAX_SIDECAR_BYTES = 4 * 1024 * 1024
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_MANIFESTS = 12000
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def read_bounded_json(path: Path, limit: int) -> dict:
    if path.is_symlink():
        raise ValueError("symlink metadata denied")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("not regular metadata")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("metadata changed during bounded read")
    finally:
        os.close(fd)
    if len(raw) > limit:
        raise ValueError("metadata size limit exceeded")
    data = json.loads(raw)
    if type(data) is not dict:
        raise ValueError("metadata not an object")
    return data


def range_coverage(intervals: list[tuple[int, int, str]]) -> dict:
    """Count ordered adjacent resets and union-covered holes independently."""
    if not intervals:
        return {"intervals": 0, "adjacent_overlaps": 0,
                "adjacent_forward_gaps": 0, "internal_uncovered_ranges": 0,
                "initial_uncovered_prefix": False, "malformed": 0}
    valid = [(a,b,t) for a,b,t in intervals
             if type(a) is int and type(b) is int and 0 <= a <= b]
    ordered = sorted(valid, key=lambda x: (x[2], x[0], x[1]))
    deltas = [cur[0] - prev[1] for prev,cur in zip(ordered,ordered[1:])]
    merged = []
    for a,b,_ in sorted(valid, key=lambda x:(x[0],x[1])):
        if not merged or a > merged[-1][1]:
            merged.append([a,b])
        else:
            merged[-1][1] = max(merged[-1][1], b)
    return {
        "intervals":len(valid),
        "adjacent_overlaps":sum(d < 0 for d in deltas),
        "adjacent_forward_gaps":sum(d > 0 for d in deltas),
        "internal_uncovered_ranges":max(0,len(merged)-1),
        "initial_uncovered_prefix":bool(merged and merged[0][0] > 0),
        "malformed":len(intervals)-len(valid)
    }


def summarize(state: dict, manifests: list[dict]) -> dict:
    sources = state.get("sources")
    if type(sources) is not dict:
        raise ValueError("checkpoint missing sources map")
    interval_by_key = defaultdict(list)
    diagnostics = Counter()
    for m in manifests:
        if type(m) is not dict or type(m.get("files")) is not list:
            diagnostics["malformed_manifest"] += 1
            continue
        stamp = m.get("stamp_utc")
        if type(stamp) is not str:
            diagnostics["missing_manifest_timestamp"] += 1
            stamp = ""
        for entry in m["files"]:
            if type(entry) is not dict:
                diagnostics["malformed_file_row"] += 1
                continue
            group, path = entry.get("source_group"), entry.get("source")
            if type(group) is not str or type(path) is not str:
                diagnostics["missing_source_key"] += 1
                continue
            start, end = entry.get("offset_from"), entry.get("size_at_open")
            if type(start) is not int or type(end) is not int or start < 0 or end < start:
                diagnostics["incomplete_legacy_interval"] += 1
                continue
            interval_by_key[group + ":" + path].append((start,end,stamp))
    groups = defaultdict(Counter)
    totals = Counter()
    for key, record in sources.items():
        if type(key) is not str or ":" not in key:
            totals["invalid_cursor_key"] += 1
            continue
        group = key.split(":",1)[0]
        groups[group]["cursors"] += 1
        if (type(record) is not dict or type(record.get("offset")) is not int
                or record["offset"] < 0):
            totals["invalid_cursor_record"] += 1
            continue
        if record["offset"] > 0:
            groups[group]["nonzero_offsets"] += 1
        if not all(k in record for k in ("source_dev","source_inode","prefix_sha256")):
            groups[group]["legacy_without_provenance"] += 1
        if key in interval_by_key:
            groups[group]["matched_local_manifest"] += 1
        elif record["offset"] > 0:
            groups[group]["unmatched_nonzero_offset"] += 1
        else:
            groups[group]["unmatched_zero_offset"] += 1
    for key, intervals in interval_by_key.items():
        coverage = range_coverage(intervals)
        totals["archive_source_keys"] += 1
        totals["archival_rows_claimed"] += coverage["intervals"]
        totals["adjacent_overlap_or_reset_transitions"] += coverage["adjacent_overlaps"]
        totals["adjacent_forward_gap_transitions"] += coverage["adjacent_forward_gaps"]
        totals["internal_uncovered_ranges_after_union"] += coverage["internal_uncovered_ranges"]
        totals["sources_lacking_offset_zero_history"] += int(coverage["initial_uncovered_prefix"])
        totals["malformed_ranges"] += coverage["malformed"]
    return {
        "scope":"local sidecar metadata only; not independent history proof",
        "historical_committed_end_authenticated":False,
        "source_payload_bytes_read":0,
        "archive_payload_bytes_read":0,
        "source_paths_in_output":False,
        "checkpoint_entries":len(sources),
        "manifest_count":len(manifests),
        "group_summary":{g:dict(c) for g,c in sorted(groups.items())},
        "coverage":dict(totals),
        "metadata_diagnostics":dict(diagnostics),
        "migration_approved":False
    }



def inventory(spool: Path) -> dict:
    """Read a bounded local checkpoint and manifest sidecars, never raw traces."""
    if not spool.is_dir() or spool.is_symlink():
        raise ValueError("invalid spool root")
    root = spool / "sealed"
    if not root.is_dir() or root.is_symlink():
        raise ValueError("invalid sealed metadata directory")
    state = read_bounded_json(spool/"state"/"offsets.json",MAX_STATE_BYTES)
    sidecars = []
    consumed = 0
    observed = 0
    with os.scandir(root) as scan:
        for ent in scan:
            if not ent.name.endswith(".tar.zst.manifest.json"):
                continue
            observed += 1
            if observed > MAX_MANIFESTS:
                raise ValueError("too many sidecars")
            if not ent.is_file(follow_symlinks=False):
                raise ValueError("nonregular manifest entry")
            size = ent.stat(follow_symlinks=False).st_size
            if size > MAX_SIDECAR_BYTES or consumed + size > MAX_TOTAL_BYTES:
                raise ValueError("bounded manifest scan exceeded limit")
            consumed += size
            sidecars.append(read_bounded_json(root/ent.name,MAX_SIDECAR_BYTES))
    result = summarize(state,sidecars)
    result["metadata_bytes_declared"] = consumed
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: source_history_coverage.py /absolute/local/spool")
    print(json.dumps(inventory(Path(sys.argv[1])),sort_keys=True,indent=2))