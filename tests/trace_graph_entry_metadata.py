"""Exact Neo4j server-OBSERVED transaction metadata binding (research only).

The trusted gateway, not a worker, must create transaction metadata and own
the only graph connection. Metadata is not a signed server-issued permit.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from trace_graph_entry_research import BoundAttempt

_TXID = re.compile(r"^neo4j-transaction-[0-9]+$")


def gateway_metadata(attempt: BoundAttempt, plan_id: str) -> dict[str, object]:
    if not plan_id or not attempt.operation_id or not attempt.attempt_id:
        raise ValueError("INVALID_ATTEMPT")
    return {
        "assistx_schema": "research-gateway-entry-v1",
        "assistx_operation_id": attempt.operation_id,
        "assistx_attempt_id": attempt.attempt_id,
        "assistx_reservation_id": attempt.grant.reservation_id,
        "assistx_epoch": attempt.grant.epoch,
        "assistx_term": attempt.grant.term,
        "assistx_plan_id": plan_id,
    }


def observed_exact(
    rows: Iterable[Mapping[str, object]],
    required: Mapping[str, object],
    database: str = "neo4j",
) -> str | None:
    """Fail closed if 0 or >1 exact server-visible matching transactions.

    Does NOT infer closure, prevent metaData spoofing by an authorized gateway,
    or establish server identity across restarts/partitions.
    """
    if database != "neo4j" or not required or not all(
        isinstance(k, str) and k.startswith("assistx_") for k in required
    ):
        return None
    found: list[str] = []
    for row in rows:
        if row.get("database") != database:
            continue
        txid = row.get("transactionId")
        meta = row.get("metaData")
        if not isinstance(txid, str) or not _TXID.fullmatch(txid):
            continue
        if not isinstance(meta, dict):
            continue
        if all(k in meta and meta[k] == v and type(meta[k]) is type(v)
               for k, v in required.items()):
            found.append(txid)
    return found[0] if len(found) == 1 else None
