#!/usr/bin/env python3
"""Read canonical AssistX admission evidence without creating or renewing authority.

This tool never runs provider inference, writes Neo4j, or reads raw traces.
Presence of expired or admitted records does not establish physical readiness.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
from typing import Any

LABELS = (
    ("RuntimeInstance", "admitted"),
    ("LoadedModelInstance", "admitted"),
    ("AccessPath", "approved"),
    ("CapacityObservation", "approved"),
)
STATE_QUERY = """
MATCH (s:FleetProjectionState {name:'canonical'})
RETURN s.generation AS generation,
       s.status AS status,
       s.expires_at_ts AS expires_at_ts,
       s.updated_at_ts AS updated_at_ts,
       s.approved_by AS approved_by,
       s.approval_id AS approval_id
"""
# Literal labels selected only from the compiled fixed list, not user input.
COUNT_QUERY = """
MATCH (n:{label})
RETURN count(n) AS total,
       sum(CASE WHEN n.projection_generation=$generation THEN 1 ELSE 0 END)
         AS canonical_generation,
       sum(CASE WHEN n.expires_at_ts>$now_ms THEN 1 ELSE 0 END)
         AS unexpired,
       sum(CASE WHEN n.projection_generation=$generation
                 AND n.expires_at_ts>$now_ms
                 AND n.{approval}=true THEN 1 ELSE 0 END)
         AS eligible_by_fields
"""
# Read-only diagnostic does not verify graph edges, signatures, keys,
# endpoint liveness, or per-runtime model identity coherence.


def positive_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def inspect(session: Any, now_ms: int) -> dict[str, Any]:
    if type(now_ms) is not int or now_ms <= 0:
        raise ValueError("now_ms must be positive")
    state_record = session.run(STATE_QUERY).single()
    state = dict(state_record) if state_record else {}
    generation = positive_int(state.get("generation"))
    expiry = positive_int(state.get("expires_at_ts"))
    approved = (
        state.get("status") == "approved"
        and generation is not None
        and expiry is not None
        and expiry > now_ms
        and bool(str(state.get("approved_by") or "").strip())
        and bool(str(state.get("approval_id") or "").strip())
    )
    result: dict[str, Any] = {
        "kind": "canonical-runtime-admission-readonly-v1",
        "checked_at_utc": datetime.datetime.fromtimestamp(
            now_ms / 1000, datetime.timezone.utc
        ).isoformat(),
        "canonical_generation": generation,
        "canonical_status": state.get("status") if isinstance(state.get("status"), str) else None,
        "canonical_expiry_utc": (
            datetime.datetime.fromtimestamp(expiry / 1000, datetime.timezone.utc).isoformat()
            if expiry else None
        ),
        "canonical_expired_by_seconds": (
            max(0, (now_ms - expiry) // 1000) if expiry else None
        ),
        "canonical_approval_complete_and_fresh": approved,
        "record_counts": {},
        "can_dispatch": False,
        "authorizes_approval": False,
        "model_requests_sent": 0,
        "database_writes": 0,
        "limitations": [
            "Aggregate records do not prove graph relationships or signatures",
            "Physical process, model artifact, and dual-path checks are separate",
            "Only AssistX's signed projection and router policy decide dispatch",
        ],
        "gate": "HOLD",
        "reasons": [],
    }
    if not state:
        result["reasons"].append("canonical-state-missing")
    elif generation is None:
        result["reasons"].append("canonical-generation-invalid")
    elif expiry is None or expiry <= now_ms:
        result["reasons"].append("canonical-approval-expired-or-absent")
    elif not approved:
        result["reasons"].append("canonical-approval-identity-or-status-invalid")
    all_eligible = True
    for label, approval in LABELS:
        row = session.run(
            COUNT_QUERY.format(label=label, approval=approval),
            generation=generation if generation is not None else -1,
            now_ms=now_ms,
        ).single()
        counts = dict(row) if row else {}
        entry = {
            name: int(counts.get(name) or 0)
            for name in ("total", "canonical_generation", "unexpired", "eligible_by_fields")
        }
        result["record_counts"][label] = entry
        if entry["eligible_by_fields"] <= 0:
            all_eligible = False
            result["reasons"].append(f"{label}-no-current-eligible-records")
    if approved and all_eligible:
        result["gate"] = "HOLD_READINESS_NOT_PROVEN"
        result["reasons"].append("physical-topology-signature-and-router-readback-not-verified")
    return result


def main() -> int:
    if sys.argv != [sys.argv[0]]:
        print("usage: inspect-canonical-admission-readonly.py (no flags or actions)", file=sys.stderr)
        return 2
    try:
        from neo4j import GraphDatabase, READ_ACCESS

        uri = os.environ["NEO4J_URI"]
        username = os.environ["NEO4J_USER"]
        password = os.environ["NEO4J_PASSWORD"]
        database = os.environ.get("NEO4J_DATABASE", "neo4j")
        if not uri or not username or not password:
            raise ValueError("missing database connection configuration")
        driver = GraphDatabase.driver(uri, auth=(username, password))
        try:
            with driver.session(
                database=database, default_access_mode=READ_ACCESS
            ) as session:
                report = inspect(session, int(time.time() * 1000))
        finally:
            driver.close()
    except Exception as exc:
        # Never print connection URLs, credentials, query values, or server bodies.
        print(json.dumps({
            "kind": "canonical-runtime-admission-readonly-v1",
            "gate": "UNAVAILABLE",
            "can_dispatch": False,
            "authorizes_approval": False,
            "error_type": type(exc).__name__,
            "database_writes": 0,
            "model_requests_sent": 0,
        }, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
