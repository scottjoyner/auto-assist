"""Atomic Neo4j *source* receipt research, never external custody.

For enrolled heartbeat/completion only, records an opaque-keyed,
request-digest-bound receipt in the SAME committed transaction as Task
mutation and owner source generation increment. A caller whose socket reply
is lost can query a receipt key and matching exact request without repeating
a Task mutation. Neo4j-only persistence is not an independent signer,
complete writer census, unforgeable revocation, external effect proof,
production endpoint, or replacement for exclusive broker credential custody.

The receipt uniqueness constraint is an independently installed prerequisite.
This module is never used by production API/worker/adapter.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .hermes_source_generation_research import (
    OWNER, GenerationProtocolError, _check_constraint,
    advance_candidate_generation,
)
from .hermes_source_heartbeat_research import (
    _RENEW, _ID, MIN_TTL_MS, MAX_TTL_MS,
)
from .hermes_source_completion_research import _COMPLETE

RECEIPT_CONSTRAINT="hermes_source_receipt_key_unique"
_DOMAIN=b"hermes-source-receipt-candidate-v1\0"
_OPERATIONS=frozenset(("heartbeat","complete"))


def _normalized(
    *, operation: str, receipt_key: str, expected_generation: int,
    task_id: str, claim_id: str, session_id: str, attempt: int,
    ttl_ms: int | None,
) -> tuple[str, dict[str, Any]]:
    if (type(operation) is not str or operation not in _OPERATIONS
            or type(receipt_key) is not str
            or type(expected_generation) is not int or expected_generation < 0
            or type(attempt) is not int or attempt < 1
            or any(type(v) is not str or not _ID.fullmatch(v)
                   for v in (task_id,claim_id,session_id))
            or (operation=="heartbeat"
                and (type(ttl_ms) is not int or
                     not MIN_TTL_MS<=ttl_ms<=MAX_TTL_MS))
            or (operation=="complete" and ttl_ms is not None)):
        raise GenerationProtocolError("invalid_source_receipt_request")
    try:
        parsed=uuid.UUID(receipt_key)
    except (ValueError,TypeError,AttributeError) as exc:
        raise GenerationProtocolError("invalid_source_receipt_request") from None
    if parsed.version != 4 or str(parsed)!=receipt_key:
        raise GenerationProtocolError("invalid_source_receipt_request")
    request={
        "operation":operation,
        "expected_generation":expected_generation,
        "task_id":task_id,"claim_id":claim_id,
        "session_id":session_id,"attempt":attempt,
    }
    if operation=="heartbeat":
        request["ttl_ms"]=ttl_ms
    digest=hashlib.sha256(
        _DOMAIN+json.dumps(request,sort_keys=True,
                           separators=(",",":"),ensure_ascii=True).encode()
    ).hexdigest()
    return digest,request


def _require_receipt_constraint(session: Any) -> None:
    row=session.run("""
        SHOW CONSTRAINTS
        YIELD name, type, labelsOrTypes, properties
        WHERE name=$name
        RETURN name,type,labelsOrTypes,properties LIMIT 1
    """,{"name":RECEIPT_CONSTRAINT}).single()
    if (not row or row.get("type")!="UNIQUENESS"
            or list(row.get("labelsOrTypes") or [])!=["HermesSourceReceipt"]
            or list(row.get("properties") or [])!=["key"]):
        raise GenerationProtocolError("source_receipt_uniqueness_constraint_missing")


def _receipt_result(generation: int, *, reconciled: bool) -> dict[str, Any]:
    return {
        "schema":1,
        "candidate_generation":generation,
        "source_receipt_committed":True,
        "source_receipt_reconciled":reconciled,
        "source_database_only":True,
        "socket_delivery_durable":False,
        "independent_custody_verified":False,
        "all_writers_generation_fenced":False,
        "resource_effects_verified":False,
        "production_dispatch_authorized":False,
        "decision":"HOLD",
    }


def commit_source_receipt(
    session: Any, *, operation: str, receipt_key: str,
    expected_generation: int, task_id: str, claim_id: str,
    session_id: str, attempt: int, ttl_ms: int | None=None,
) -> dict[str, Any]:
    """One fixed Task update, receipt CREATE and generation CAS transaction.

    Receipt persists ONLY when the Task and source generation commit.
    Requests cannot create receipts for rejected/stale Tasks.
    Caller must use reconcile_source_receipt (not another mutation)
    following an ambiguous client socket result.
    """
    digest,request=_normalized(
        operation=operation,receipt_key=receipt_key,
        expected_generation=expected_generation,
        task_id=task_id,claim_id=claim_id,
        session_id=session_id,attempt=attempt,ttl_ms=ttl_ms)
    _check_constraint(session)
    _require_receipt_constraint(session)
    verified=False

    def mutate(tx: Any) -> bool:
        nonlocal verified
        query=_RENEW if operation=="heartbeat" else _COMPLETE
        args={
            "owner":OWNER,"task_id":task_id,"claim_id":claim_id,
            "session_id":session_id,"attempt":attempt,
        }
        if operation=="heartbeat":
            args["ttl_ms"]=ttl_ms
        row=tx.run(query,args).single()
        verified=bool(
            row and row.get("task_id")==task_id
            and row.get("claim_id")==claim_id
            and row.get("session_id")==session_id
            and type(row.get("attempt")) is int
            and row.get("attempt")==attempt
            and (
                (operation=="heartbeat"
                 and row.get("status") in ("CLAIMED","RUNNING")
                 and row.get("heartbeat_by")==OWNER
                 and type(row.get("db_now")) is int
                 and type(row.get("heartbeat_at_ts")) is int
                 and row["heartbeat_at_ts"]==row["db_now"]
                 and type(row.get("lease_expiry")) is int
                 and row["lease_expiry"]>=row["db_now"]+ttl_ms)
                or
                (operation=="complete"
                 and row.get("status")=="DONE"
                 and row.get("completed_by")==OWNER)
            )
        )
        if not verified:
            return False
        recorded=tx.run("""
            CREATE(r:HermesSourceReceipt {
                key:$key, owner:$owner, operation:$operation,
                request_digest:$digest, source_generation:$generation,
                created_at_ts:timestamp()
            })
            RETURN r.key AS key, r.source_generation AS generation,
                   r.request_digest AS digest
        """,{
            "key":receipt_key,"owner":OWNER,
            "operation":operation,"digest":digest,
            "generation":expected_generation+1,
        }).single()
        return bool(
            recorded and recorded.get("key")==receipt_key
            and type(recorded.get("generation")) is int
            and recorded["generation"]==expected_generation+1
            and recorded.get("digest")==digest
        )

    advanced=advance_candidate_generation(
        session,expected_generation=expected_generation,mutate=mutate)
    if verified is not True or advanced.get("generation")!=expected_generation+1:
        raise GenerationProtocolError("source_receipt_commit_unverified")
    return _receipt_result(expected_generation+1,reconciled=False)


def reconcile_source_receipt(
    session: Any, *, operation: str, receipt_key: str,
    expected_generation: int, task_id: str, claim_id: str,
    session_id: str, attempt: int, ttl_ms: int | None=None,
) -> dict[str, Any]:
    """Exact request digest lookup; no Task mutation, ledger increment, or retry.

    A missing/mismatched record does NOT prove a Task was not changed.
    Only the source Neo4j transaction result is confirmed when found.
    """
    digest,_=_normalized(
        operation=operation,receipt_key=receipt_key,
        expected_generation=expected_generation,
        task_id=task_id,claim_id=claim_id,session_id=session_id,
        attempt=attempt,ttl_ms=ttl_ms)
    _check_constraint(session)
    _require_receipt_constraint(session)
    row=session.run("""
        MATCH(r:HermesSourceReceipt {key:$key,owner:$owner})
        WHERE r.request_digest=$digest AND r.operation=$operation
          AND r.source_generation=$generation
        RETURN r.key AS key,r.source_generation AS generation
        LIMIT 1
    """,{
        "key":receipt_key,"owner":OWNER,"digest":digest,
        "operation":operation,"generation":expected_generation+1,
    }).single()
    if not row or row.get("key")!=receipt_key or (
            type(row.get("generation")) is not int or
            row["generation"]!=expected_generation+1):
        raise GenerationProtocolError("source_receipt_not_verified")
    return _receipt_result(expected_generation+1,reconciled=True)
