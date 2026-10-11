"""Manual-only broker-owned explicit Bolt transaction, no witness/signature.

Only called by the already-admitted fixed-plan handler with a genuine
three-host Raft reservation. Holds a read-only explicit Bolt transaction
while a separate read-only observer identifies original metadata.
A timeout or driver error raises and leaves Raft capacity RESERVED.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import time

from neo4j import GraphDatabase

FIELDS = frozenset({
    "assistx_schema", "assistx_operation_id", "assistx_attempt_id",
    "assistx_reservation_id", "assistx_epoch", "assistx_term",
    "assistx_plan_id",
})
RELEASE = Path("/tmp/b06-release")
TARGET = Path("/tmp/b06-target.json")


def held_task(task_id: str, reader_user: str, reader_password: str,
              metadata: dict) -> dict:
    if (set(metadata) != FIELDS or
            metadata.get("assistx_schema") != "research-gateway-entry-v1" or
            metadata.get("assistx_plan_id") != "get_task_read" or
            not re.fullmatch(r"[0-9a-f]{32}",
                             str(metadata.get("assistx_reservation_id"))) or
            RELEASE.exists() or TARGET.exists()):
        raise ValueError("B06_EXPLICIT_TRANSACTION_REFUSED")
    with GraphDatabase.driver(
            "bolt://neo4j:7687", auth=(reader_user, reader_password),
            connection_timeout=4) as driver:
        with driver.session(database="neo4j") as session:
            # Neo4j Python driver attaches this metadata to SHOW TRANSACTIONS.
            # The worker cannot supply or edit it.
            with session.begin_transaction(metadata=metadata, timeout=42) as tx:
                result = tx.run(
                    "MATCH (t:Task {id: $id}) "
                    "RETURN t { .id, .status } AS task", id=task_id).single()
                task = dict(result["task"]) if result is not None else None
                if task is None or set(task) != {"id", "status"}:
                    raise ValueError("B06_GRAPH_TASK_NOT_FOUND")
                # Signal only after the Bolt query was physically executed.
                # Not an independently authenticated graph boot identity.
                TARGET.write_text(json.dumps({
                    "metadata": metadata, "graph_member": "disposable-neo4j",
                    "graph_boot": "unattested-fixture-boot",
                }, sort_keys=True))
                deadline = time.monotonic() + 22
                while not RELEASE.is_file():
                    if time.monotonic() > deadline:
                        raise TimeoutError("B06_OBSERVER_DID_NOT_ACK")
                    time.sleep(.12)
                # No Raft bind/close/sign; a successful read is never release.
                tx.commit()
                return task
