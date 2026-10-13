"""HOLD-only B02 counts canary. Fixed, non-tenant, no Task properties emitted.
Not a replacement for the harness evolution page, a broker, or a mounted route.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TASK_KINDS = (
    "harness_reflect", "harness_train", "harness_deploy",
    "harness_rescore", "adaptive_model_benchmark",
)
STATUSES = ("READY", "CLAIMED", "RUNNING", "DONE", "FAILED", "ERROR")
PLAN_ID = "harness_evolution_aggregate_counts_research_v1"
QUERY = (
    "MATCH (t:Task) "
    "WHERE t.kind IN $kinds AND t.status IN $statuses "
    "RETURN t.kind AS kind, t.status AS status, count(t) AS n "
    "ORDER BY kind, status"
)
MAX_ROWS = len(TASK_KINDS) * len(STATUSES)

class CountsDenied(ValueError):
    pass

def fixed_params() -> dict:
    return {"kinds": list(TASK_KINDS), "statuses": list(STATUSES)}

def validate_rows(rows: Any) -> dict:
    """Explicit 30-cell allowlist with no row-level property values."""
    if type(rows) is not list or len(rows) > MAX_ROWS:
        raise CountsDenied("UNREVIEWED_AGGREGATE_RESULT")
    cells = {(kind, status): 0 for kind in TASK_KINDS for status in STATUSES}
    seen = set()
    for row in rows:
        if type(row) is not dict or set(row) != {"kind", "status", "n"}:
            raise CountsDenied("UNREVIEWED_AGGREGATE_FIELDS")
        kind, status, n = row["kind"], row["status"], row["n"]
        if (type(kind) is not str or kind not in TASK_KINDS
                or type(status) is not str or status not in STATUSES
                or type(n) is not int or n < 0
                or n > 100_000_000 or (kind, status) in seen):
            raise CountsDenied("INVALID_AGGREGATE_VALUE")
        seen.add((kind, status))
        cells[(kind, status)] = n
    return {
        "schema": "assistx-b02-harness-counts-research-v1",
        "result": "COUNTS_ONLY_READ_CANARY",
        "plan": PLAN_ID,
        "matching_task_count": sum(cells.values()),
        "kind_counts": {kind: sum(cells[kind, status] for status in STATUSES)
                        for kind in TASK_KINDS},
        "status_counts": {status: sum(cells[kind, status] for kind in TASK_KINDS)
                          for status in STATUSES},
        "max_result_rows": MAX_ROWS,
        "contains_task_values": False,
        "real_data_global_operator_access_authorized": False,
        "user_facing_harness_equivalence_proven": False,
        "production_http_route_enabled": False,
        "tenant_isolation_proven": False,
        "network_cutover_authorized": False,
        "reservation_release_authorized": False,
    }
