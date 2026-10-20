"""Issue #118: bounded aggregate-only local/NAS-inventory retention triage.

Never proves per-source custody, opens archive payloads, contacts NAS or
accepts migration. Inputs are locally cached JSON summaries only.
"""
from __future__ import annotations

import json
from pathlib import Path


def _natural(value):
    return type(value) is int and value >= 0


def triage(local: dict, cached_archive: dict) -> dict:
    if type(local) is not dict or type(cached_archive) is not dict:
        raise ValueError("aggregate input must be objects")
    groups = local.get("group_summary")
    coverage = cached_archive.get("sourceGroupCoverage")
    never_seen = cached_archive.get("groupsNeverSeen")
    if type(groups) is not dict or type(coverage) is not dict or type(never_seen) is not list:
        raise ValueError("incomplete inventories")
    if any(type(g) is not str for g in never_seen):
        raise ValueError("invalid never-seen group name")
    never = set(never_seen)
    if never & set(coverage):
        raise ValueError("cached group coverage contradicts never-seen list")
    archive_stats = {}
    for name in ("archivedSegments","inboxSegments","sha256Verified","deepVerifiedThisRun"):
        value = cached_archive.get(name)
        if not _natural(value):
            raise ValueError("missing verified inventory count " + name)
        archive_stats[name] = value
    if archive_stats["sha256Verified"] > archive_stats["archivedSegments"]:
        raise ValueError("verified count exceeds archived")
    report_groups = {}
    unmatched_without_group = 0
    unmatched_with_group = 0
    for name,entry in sorted(groups.items()):
        if type(name) is not str or type(entry) is not dict:
            raise ValueError("bad local group")
        counts = {}
        for field in ("cursors","matched_local_manifest","unmatched_nonzero_offset",
                      "unmatched_zero_offset","legacy_without_provenance"):
            n=entry.get(field,0)
            if not _natural(n):
                raise ValueError("invalid group count")
            counts[field] = n
        if counts["matched_local_manifest"] + counts["unmatched_nonzero_offset"] + counts["unmatched_zero_offset"] > counts["cursors"]:
            raise ValueError("source counts inconsistent")
        historical_segments = coverage.get(name,0)
        if not _natural(historical_segments):
            raise ValueError("invalid historical segment count")
        if name in never:
            statement="NOT_SEEN_IN_CACHED_ARCHIVE_GROUP_INDEX"
            unmatched_without_group += counts["unmatched_nonzero_offset"]
        elif historical_segments > 0:
            statement="GROUP_PRESENT_ONLY_SOURCE_LINEAGE_UNPROVEN"
            unmatched_with_group += counts["unmatched_nonzero_offset"]
        else:
            statement="NOT_LISTED_IN_CACHED_ARCHIVE_GROUP_INDEX"
            unmatched_without_group += counts["unmatched_nonzero_offset"]
        report_groups[name] = {
            **counts,
            "cached_archived_group_segments":historical_segments,
            "archive_inventory_status":statement,
            "per_source_custody_proven":False
        }
    return {
        "checked_utc_from_cached_inventory":cached_archive.get("checkedUtc"),
        "inventory_is_live_proof":False,
        "archive_inventory_state":cached_archive.get("state"),
        "cached_archive_summary":archive_stats,
        "cached_capture_holes":len(cached_archive.get("captureHoles",[])),
        "groups":report_groups,
        "unmatched_nonzero_without_archive_group_evidence":unmatched_without_group,
        "unmatched_nonzero_with_archive_group_only_evidence":unmatched_with_group,
        "source_names_or_paths_emitted":False,
        "source_bytes_opened":0,
        "archive_payload_bytes_opened":0,
        "migration_authorized":False
    }


def run(local_json: Path, cached_archive_json: Path) -> dict:
    from source_history_coverage import read_bounded_json
    local = read_bounded_json(local_json, 8*1024*1024)
    cached = read_bounded_json(cached_archive_json, 8*1024*1024)
    return triage(local, cached)


if __name__=="__main__":
    import sys
    if len(sys.argv)!=3:
        raise SystemExit("usage: triage.py /path/local-aggregate.json /path/cache-index.json")
    print(json.dumps(run(Path(sys.argv[1]),Path(sys.argv[2])),indent=2,sort_keys=True))

