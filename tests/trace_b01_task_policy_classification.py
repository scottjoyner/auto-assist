"""Synthetic-only B01 policy-classification proposal; no route or graph driver.

Consumes a *fixed six-field* internal broker Task policy projection. No
payload/required-capabilities content is transmitted in the result.
Executes source-pinned pure API helpers; never adjudicates a claim/write,
and never substitutes for authenticated identity or atomic fencing.
"""
from __future__ import annotations
from pathlib import Path
import json
import re

from trace_b01_task_consumer_parity import (
    BROKER_CLAIM_FIELDS, compile_source_helpers,
)

SCHEMA = "assistx-b01-task-policy-classification-v0"
ID_PATTERN = re.compile(r"fixture-task-[A-Za-z0-9_-]{4,48}")
TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9_:-]{0,63}")
KNOWN_QUEUES = frozenset(("interactive", "batch", "critical"))
KNOWN_STATUSES = frozenset((
    "READY", "CLAIMED", "RUNNING", "DONE", "FAILED", "ERROR",
))
MAX_JSON_BYTES = 16384

class ClassificationDenied(ValueError):
    pass


def _unique_json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ClassificationDenied("DUPLICATE_POLICY_JSON_KEY")
        result[name] = value
    return result


def _json_object(value: object, field: str) -> dict:
    if value is None:
        return {}
    if type(value) is dict:
        result = value
    elif type(value) is str and len(value.encode()) <= MAX_JSON_BYTES:
        try:
            result = json.loads(value, object_pairs_hook=_unique_json_object)
        except ClassificationDenied:
            raise
        except (ValueError, RecursionError):
            raise ClassificationDenied("MALFORMED_"+field) from None
    else:
        raise ClassificationDenied("UNREVIEWED_"+field+"_TYPE_OR_SIZE")
    if type(result) is not dict:
        raise ClassificationDenied("NONOBJECT_"+field)
    try:
        encoded_len = len(json.dumps(result).encode())
    except (TypeError, ValueError, RecursionError):
        raise ClassificationDenied("UNSERIALIZABLE_"+field) from None
    if encoded_len > MAX_JSON_BYTES:
        raise ClassificationDenied("OVERSIZED_"+field)
    if "runbook" in result:
        runbook = result["runbook"]
        if runbook is not None and (
                type(runbook) is not str or len(runbook.encode()) > 256):
            raise ClassificationDenied("UNREVIEWED_RUNBOOK")
    if "queue_class" in result and (
            type(result["queue_class"]) is not str
            or result["queue_class"] not in KNOWN_QUEUES):
        raise ClassificationDenied("UNREVIEWED_PAYLOAD_QUEUE_CLASS")
    return result
def classify_policy(raw: object, *, api_source: Path) -> dict:
    if type(raw) is not dict or set(raw) != BROKER_CLAIM_FIELDS:
        raise ClassificationDenied("REQUIRED_POLICY_PROJECTION_INCOMPLETE")
    task_id = raw["id"]
    if type(task_id) is not str or ID_PATTERN.fullmatch(task_id) is None:
        raise ClassificationDenied("UNREVIEWED_SYNTHETIC_TASK_ID")
    if type(raw["status"]) is not str or raw["status"] not in KNOWN_STATUSES:
        raise ClassificationDenied("UNREVIEWED_TASK_STATUS")
    caps = raw["required_capabilities"]
    if caps is None:
        caps=[]
    if (type(caps) is not list or len(caps) > 32
            or any(type(x) is not str or TOKEN_PATTERN.fullmatch(x) is None
                   for x in caps)):
        raise ClassificationDenied("UNREVIEWED_REQUIRED_CAPABILITIES")
    if (raw["queue_class"] is not None
            and (type(raw["queue_class"]) is not str
                 or raw["queue_class"] not in KNOWN_QUEUES)):
        raise ClassificationDenied("UNREVIEWED_TASK_QUEUE_CLASS")
    payload = _json_object(raw["payload"], "PAYLOAD")
    payload_json = _json_object(raw["payload_json"], "PAYLOAD_JSON")
    # Existing API has a first-truthy-payload precedence for runbook while
    # queue_class prefers payload_json. Do not silently accept contradictory
    # sources during a migration that could weaken recovery identity checks.
    if (raw["payload"] is not None and raw["payload_json"] is not None
            and bool(payload.get("runbook")) != bool(payload_json.get("runbook"))):
        raise ClassificationDenied("AMBIGUOUS_RECOVERY_PAYLOAD_SOURCES")
    if (raw["queue_class"] is not None
            and payload_json.get("queue_class") is not None
            and raw["queue_class"] != payload_json["queue_class"]):
        raise ClassificationDenied("AMBIGUOUS_QUEUE_CLASS_SOURCES")
    helpers=compile_source_helpers(api_source, mode="drain")
    recovery=helpers._is_recovery_task(raw)
    allowed, _reason = helpers._is_claim_allowed_for_workflow_control(raw)
    return {
        "schema":SCHEMA,
        "id":task_id,
        "status":raw["status"],
        "requires_recovery_node_identity":bool(recovery),
        "drain_mode_policy_candidate":bool(allowed),
        "claim_authorized":False,
        "resource_fence_verified":False,
        "release_authorized":False,
    }