"""Read-only issue #118 drain/admission and historical custody gate.

Consumes locally cached status metadata only; never contacts /nas or changes
collector, drainer, retained segments, source state, or approvals.
"""
from __future__ import annotations

import json
from pathlib import Path
from source_history_coverage import read_bounded_json


def _count(obj,field):
    value=obj.get(field)
    if type(value) is not int or value<0:
        raise ValueError("invalid drain counter: "+field)
    return value


def assess(safe:dict,watch:dict,inventory:dict,coverage:dict)->dict:
    for doc in (safe,watch,inventory,coverage):
        if type(doc) is not dict:
            raise ValueError("invalid metadata")
    pending=_count(safe,"pending_files")
    ack_pending=_count(watch,"drainBacklogPendingAck")
    ack_retained=_count(watch,"drainRetainedAcked")
    raw_backlog=_count(watch,"drainBacklogRaw")
    if raw_backlog < ack_pending + ack_retained:
        raise ValueError("backlog counters violate custody partition")
    groups=coverage.get("groups")
    if type(groups) is not dict:
        raise ValueError("missing coverage groups")
    unmatched=sum(_count(d,"unmatched_nonzero_offset") for d in groups.values())
    never_group=_count(coverage,"unmatched_nonzero_without_archive_group_evidence")
    if never_group > unmatched:
        raise ValueError("tiered inventory source mismatch")
    index_verified=_count(inventory,"sha256Verified")
    index_archived=_count(inventory,"archivedSegments")
    if index_verified>index_archived:
        raise ValueError("verified count exceeds archive count")
    reasons=[]
    if safe.get("verdict") != "ok":
        reasons.append("SAFE_DRAIN_NOT_ADMITTED")
    if pending>0 or ack_pending>0:
        reasons.append("PENDING_UNACKNOWLEDGED_SEGMENTS")
    if raw_backlog>0:
        reasons.append("LOCAL_RETAINED_BACKLOG")
    if inventory.get("state") != "ok":
        reasons.append("CACHED_INVENTORY_NOT_HEALTHY")
    if index_verified < index_archived:
        reasons.append("CACHED_ARCHIVES_NOT_ALL_SHA_VERIFIED")
    if unmatched>0:
        reasons.append("SOURCE_HISTORY_NOT_RECONCILED")
    if inventory.get("captureHoles"):
        reasons.append("ARCHIVE_CAPTURE_HOLE_NOT_CLEARED")
    if safe.get("automatic_cleanup_permitted") is not False:
        reasons.append("CLEANUP_GUARD_NOT_EXPLICITLY_DISARMED")
    return {
        "scope":"cached status and aggregate-only metadata, not NAS admission",
        "nas_access_performed":False,
        "source_path_output":False,
        "safe_drain_verdict":safe.get("verdict"),
        "safe_drain_pending_files":pending,
        "watchdog_pending_ack":ack_pending,
        "watchdog_retained_acked":ack_retained,
        "watchdog_raw_backlog":raw_backlog,
        "watchdog_state":watch.get("state"),
        "cached_archive_inventory_state":inventory.get("state"),
        "cached_archived_segments":index_archived,
        "cached_sha256_verified":index_verified,
        "cached_inventory_checked_utc":inventory.get("checkedUtc"),
        "unmatched_nonzero_source_cursors":unmatched,
        "unmatched_without_cached_group_evidence":never_group,
        "reasons":reasons,
        "gate":"HOLD" if reasons else "REVIEW_ONLY",
        "automatic_source_release_allowed":False,
        "production_migration_authorized":False,
        "archive_deletion_authorized":False
    }


def run(state_dir:Path,coverage_path:Path)->dict:
    docs={}
    for name,p in {
        "safe":state_dir/"safe-drain-status.json",
        "watch":state_dir/"watchdog.json",
        "inventory":state_dir/"archive-inventory.json",
        "coverage":coverage_path
    }.items():
        docs[name]=read_bounded_json(p, 8*1024*1024)
    return assess(**docs)


if __name__=="__main__":
    import sys
    if len(sys.argv)!=3:
        raise SystemExit("usage: safe_drain_readiness.py /local/spool/state /local/aggregate.json")
    print(json.dumps(run(Path(sys.argv[1]),Path(sys.argv[2])),sort_keys=True,indent=2))

