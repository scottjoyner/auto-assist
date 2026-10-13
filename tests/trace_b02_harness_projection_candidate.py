"""B02 read-only harness/evolution Task projection candidate, OFFLINE ONLY.
No database driver, socket, broker, credential, graph writes or deployment hook.
"""
from __future__ import annotations
import json
from typing import Any

STATUS = frozenset(("READY","CLAIMED","RUNNING","DONE","FAILED","ERROR"))
KEYS = frozenset(("id","kind","title","status","target_agent_id","payload_json",
                  "updated_at_ts","summary","last_error"))
PLAN_ID = "harness_evolution_tasks_read_v1"
# This fixed query is a design witness; it is NEVER executed by this module.
CYPHER = (
    "MATCH (t:Task {status: $status}) "
    "RETURN t { .id, .kind, .title, .status, .target_agent_id, "
    ".payload_json, .updated_at_ts, .summary, .last_error } AS task "
    "ORDER BY t.created_at_ts DESC LIMIT $limit"
)

class ProjectionDenied(ValueError):
    pass

def params(status: str, limit: int = 40) -> dict:
    if type(status) is not str or status not in STATUS:
        raise ProjectionDenied("UNREVIEWED_HARNESS_STATUS")
    if type(limit) is not int or limit != 40:
        raise ProjectionDenied("UNREVIEWED_HARNESS_LIMIT")
    return {"status":status,"limit":limit}

def _validate(row: dict) -> dict:
    if type(row) is not dict or set(row) != KEYS:
        raise ProjectionDenied("MISSING_OR_EXTRA_HARNESS_FIELDS")
    if not all((row[k] is None or type(row[k]) is str)
               for k in ("id","kind","title","status","target_agent_id","summary","last_error")):
        raise ProjectionDenied("WRONG_HARNESS_FIELD_TYPE")
    if row["status"] not in STATUS:
        raise ProjectionDenied("UNREVIEWED_HARNESS_STATUS")
    if (row["payload_json"] is not None
            and type(row["payload_json"]) not in (str,dict)):
        raise ProjectionDenied("INVALID_HARNESS_PAYLOAD")
    if (type(row["payload_json"]) is str
            and len(row["payload_json"].encode()) > 64*1024):
        raise ProjectionDenied("OVERSIZED_HARNESS_PAYLOAD")
    ts=row["updated_at_ts"]
    if ts is not None and (type(ts) not in (int,float) or ts < 0):
        raise ProjectionDenied("INVALID_HARNESS_TIMESTAMP")
    return dict(row)

def project_legacy_task(row: dict) -> dict:
    """Filter a legacy raw Task dict for *offline parity only*."""
    if type(row) is not dict or not KEYS.issubset(row):
        raise ProjectionDenied("LEGACY_TASK_MISSING_FIELDS")
    return _validate({k:row[k] for k in KEYS})

def validate_broker_rows(rows: list[dict]) -> list[dict]:
    if type(rows) is not list or len(rows) > 40:
        raise ProjectionDenied("INVALID_HARNESS_RESULT_SET")
    return [_validate(row) for row in rows]

def run_shadow_parity(*, runs: list[dict], raw_tasks: list[dict],
                      snapshot_fn) -> bool:
    """Existing view function's output unchanged for representative full rows."""
    if type(raw_tasks) is not list:
        raise ProjectionDenied("INVALID_LEGACY_TASK_SET")
    raw = snapshot_fn(runs, raw_tasks)
    projected = snapshot_fn(runs, [project_legacy_task(x) for x in raw_tasks])
    if raw != projected:
        raise ProjectionDenied("HARNESS_OUTPUT_PARITY_MISMATCH")
    return True

def research_disposition() -> dict:
    return {"schema":"assistx-b02-harness-projection-research-v1",
            "candidate_plan_id":PLAN_ID,
            "physical_broker_accepted":False,
            "field_parity_for_all_live_rows_accepted":False,
            "production_graph_cutover_authorized":False,
            "release_authorized":False}
