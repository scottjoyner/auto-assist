"""Offline adapter for detached, caller-supplied AgentRun export rows.

Fields correspond to Neo4jClient.complete_task()'s AgentRun properties plus
the linked Task.id. No Neo4j connection, signature trust, or task mutation.
The export's completeness and actual graph provenance remain unverified.
"""
from collections import Counter
import hashlib
import json
from fleet_router_accounting_summary import (
    MAX_ROWS, MAX_ID_LENGTH, InvalidProjection, summarize_projections,
)

MAX_RESULT_BYTES = 256 * 1024
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 64
FIELDS = {"linked_task_id", "task_id", "run_id", "agent", "completion_id",
          "claim_id", "status", "result_json"}


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise InvalidProjection("duplicate_result_key")
        result[k] = v
    return result


def _constant(value):
    raise InvalidProjection("nonfinite_result")


def _depth(raw):
    level = 0
    quoted = escaped = False
    for b in raw:
        if quoted:
            if escaped:
                escaped = False
            elif b == 92:
                escaped = True
            elif b == 34:
                quoted = False
        elif b == 34:
            quoted = True
        elif b in (91, 123):
            level += 1
            if level > MAX_JSON_DEPTH:
                raise InvalidProjection("result_depth_limit")
        elif b in (93, 125):
            level -= 1


def _identity(value, limit=MAX_ID_LENGTH):
    return type(value) is str and 0 < len(value) <= limit


def _decode(raw):
    _depth(raw)
    try:
        result = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        raise InvalidProjection("invalid_result_json") from exc
    if type(result) is not dict:
        raise InvalidProjection("invalid_result_object")
    return result


def _project(row, raw, expected_agent):
    if type(row) is not dict or row.keys() != FIELDS:
        raise InvalidProjection("invalid_run_shape")
    if any(not _identity(row[k]) for k in ("linked_task_id", "task_id",
                                          "run_id", "agent", "claim_id")):
        raise InvalidProjection("invalid_run_identity")
    if not _identity(row["completion_id"], 2 * MAX_ID_LENGTH + 64):
        raise InvalidProjection("invalid_completion_id")
    if row["linked_task_id"] != row["task_id"]:
        raise InvalidProjection("task_relationship_mismatch")
    if row["agent"] != expected_agent:
        raise InvalidProjection("unexpected_agent")
    if type(row["status"]) is not str or row["status"] not in ("DONE", "FAILED"):
        raise InvalidProjection("unexpected_terminal_status")
    suffix = row["task_id"] + "/" + row["claim_id"]
    complete = "safe-fleet/complete/" + suffix
    failure = "safe-fleet/failure/" + suffix
    if row["completion_id"] not in (complete, failure):
        raise InvalidProjection("completion_lineage_mismatch")
    result = _decode(raw)
    if row["completion_id"] == failure:
        if row["status"] != "FAILED" or "response_accounting" in result:
            raise InvalidProjection("exception_completion_mismatch")
        return None
    if "response_accounting" not in result:
        return None  # historical/unaccounted completion, not a measured zero
    if result.get("claim_id") != row["claim_id"]:
        raise InvalidProjection("result_claim_mismatch")
    if type(result.get("exit_code")) is not int or result["exit_code"] != (0 if row["status"] == "DONE" else 1):
        raise InvalidProjection("terminal_result_mismatch")
    return {
        "task_id": row["task_id"], "claim_id": row["claim_id"],
        "projection_generation": result.get("projection_generation"),
        "projection_revision": result.get("projection_revision"),
        "response_accounting": result["response_accounting"],
    }


def summarize_agent_runs(rows, *, expected_agent):
    """Validate detached run lineage and report evidence-only usage.

    Duplicate identical export rows collapse by run ID. Distinct run IDs
    sharing one task/claim or completion ID cause HOLD, even if usage agrees.
    Malformed, conflicting or unaccounted rows suppress token totals.
    """
    if type(rows) is not list or len(rows) > MAX_ROWS or not _identity(expected_agent):
        raise InvalidProjection("batch_or_agent_limit")
    errors = Counter()
    by_run = {}
    by_attempt = {}
    by_completion = {}
    projections = []
    repeated = unaccounted = 0
    total_bytes = 0
    for row in rows:
        try:
            if type(row) is not dict or row.keys() != FIELDS:
                raise InvalidProjection("invalid_run_shape")
            text = row["result_json"]
            if type(text) is not str or len(text) > MAX_RESULT_BYTES:
                raise InvalidProjection("result_size_or_type")
            try:
                raw = text.encode("utf-8")
            except UnicodeError as exc:
                raise InvalidProjection("invalid_result_encoding") from exc
            if len(raw) > MAX_RESULT_BYTES:
                raise InvalidProjection("result_size_or_type")
            total_bytes += len(raw)
            if total_bytes > MAX_BATCH_BYTES:
                raise InvalidProjection("batch_byte_limit")
            projected = _project(row, raw, expected_agent)
            key = row["run_id"]
            lineage = tuple(row[k] for k in sorted(FIELDS - {"result_json"}))
            witness = (lineage, hashlib.sha256(raw).hexdigest())
            if key in by_run:
                if by_run[key] != witness:
                    raise InvalidProjection("conflicting_run_id")
                repeated += 1
                continue
            by_run[key] = witness
            attempt = (row["task_id"], row["claim_id"])
            completion = row["completion_id"]
            if attempt in by_attempt:
                raise InvalidProjection("multiple_runs_per_attempt")
            by_attempt[attempt] = key
            if completion in by_completion:
                raise InvalidProjection("multiple_runs_per_completion")
            by_completion[completion] = key
            if projected is None:
                unaccounted += 1
            else:
                projections.append(projected)
        except InvalidProjection as exc:
            if str(exc) == "batch_byte_limit":
                raise
            errors[str(exc)] += 1

    summary = summarize_projections(projections)
    hold = bool(errors or unaccounted or summary["decision"] == "HOLD")
    if hold:
        summary["decision"] = "HOLD"
        summary["reported_total_complete_subset"] = None
        summary["reported_total_all_attempts"] = None
    return {
        "schema": "fleet-agent-run-accounting-export-shadow-v1",
        "decision": "HOLD" if hold else summary["decision"],
        "export_rows": len(rows),
        "distinct_seen_run_ids": len(by_run),
        "identical_export_replays": repeated,
        "unaccounted_unique_runs": unaccounted,
        "lineage_errors": dict(sorted(errors.items())),
        "accounting": summary,
        "graph_provenance_verified": False,
        "export_complete_verified": False,
        "completion_authority_granted": False,
    }
