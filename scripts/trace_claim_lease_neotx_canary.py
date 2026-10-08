#!/usr/bin/env python3
"""Production Neo4j *rollback-only* claim/lease protocol test.

This script uses one uncommitted transaction per node, never calls an AssistX
task endpoint or starts a worker, and explicitly rolls back every transaction.
It tests real Neo4j Cypher semantics, not a deployed AssistX issuer.
"""

from __future__ import annotations

import json
import os
import secrets
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from neo4j import READ_ACCESS, GraphDatabase

from assistx.trace_claim_lease import issue_current_status, issue_lease_proof, verify_lease_proof
from assistx.trace_execution_adapter import TraceDenied


def exercise(driver, database: str, node: str) -> dict:
    task_id = "shadow-rollback-" + secrets.token_hex(12)
    claim_id = "claim-rollback-" + secrets.token_hex(12)
    challenge = secrets.token_hex(32)
    signer = Ed25519PrivateKey.generate()
    prepared = False
    cancelled = False
    superseded = False
    with driver.session(database=database) as session:
        tx = session.begin_transaction()
        try:
            # Entire fixture is invisible outside this open transaction.
            tx.run(
                "CREATE (t:Task {id:$id, ticket_type:'trace_probe', "
                "kind:'trace_probe', status:'READY', target_agent_id:$node, "
                "required_capabilities:['trace-probe'], "
                'payload_json:\'{"command_id":"probe.noop.v1"}\', '
                "created_at_ts:timestamp()})",
                id=task_id,
                node=node,
            ).consume()
            match = tx.run(
                "MATCH (t:Task {id:$id}) "
                "WHERE t.status='READY' AND t.target_agent_id=$node "
                "AND t.kind='trace_probe' AND t.ticket_type='trace_probe' "
                "SET t.status='CLAIMED', t.claim_id=$claim, "
                "t.claimed_by=$node, t.execution_attempt=1, "
                "t.lease_expires_at_ts=timestamp()+60000 "
                "RETURN t",
                id=task_id,
                node=node,
                claim=claim_id,
            ).single()
            if match is None:
                raise RuntimeError("graph_claim_was_not_observed_in_transaction")
            task = dict(match["t"])
            now = int(time.time() * 1000)
            proof = issue_lease_proof(
                task,
                task_id=task_id,
                claim_id=claim_id,
                node_id=node,
                execution_attempt=1,
                signer=signer,
                now_ms=now,
            )
            fresh = tx.run("MATCH (t:Task {id:$id}) RETURN t", id=task_id).single()
            current = issue_current_status(
                dict(fresh["t"]),
                lease_proof=proof,
                challenge=challenge,
                signer=signer,
                now_ms=now + 100,
            )
            verified = verify_lease_proof(
                proof,
                node_id=node,
                verifier=signer.public_key(),
                current_status=current,
                challenge=challenge,
                now_ms=now + 200,
            )
            prepared = isinstance(verified, str) and len(verified) == 64
            tx.run("MATCH (t:Task {id:$id}) SET t.status='CANCELLED'", id=task_id).consume()
            cancelled_row = tx.run("MATCH (t:Task {id:$id}) RETURN t", id=task_id).single()
            try:
                issue_current_status(
                    dict(cancelled_row["t"]),
                    lease_proof=proof,
                    challenge=challenge,
                    signer=signer,
                    now_ms=now + 300,
                )
            except TraceDenied as exc:
                cancelled = str(exc) == "claim_inactive"
            tx.run(
                "MATCH (t:Task {id:$id}) SET t.status='CLAIMED', t.claim_id='replaced-claim', t.execution_attempt=2",
                id=task_id,
            ).consume()
            swapped = tx.run("MATCH (t:Task {id:$id}) RETURN t", id=task_id).single()
            try:
                issue_current_status(
                    dict(swapped["t"]),
                    lease_proof=proof,
                    challenge=challenge,
                    signer=signer,
                    now_ms=now + 400,
                )
            except TraceDenied as exc:
                superseded = str(exc) == "claim_superseded"
        finally:
            tx.rollback()
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        remaining = session.run("MATCH (t:Task {id:$id}) RETURN count(t) AS n", id=task_id).single()["n"]
    passed = prepared and cancelled and superseded and remaining == 0
    return {
        "node_id": node,
        "ok": passed,
        "graph_claim_observed_in_tx": prepared,
        "cancelled_denied": cancelled,
        "superseded_denied": superseded,
        "rollback_confirmed": remaining == 0,
        "persisted_test_tasks": remaining,
        "executed_commands": 0,
        "real_assistx_api_claim": False,
        "signer": "ephemeral_in_process",
    }


def main() -> int:
    db = os.getenv("NEO4J_DATABASE", "assistx")
    with GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        connection_timeout=6,
    ) as driver:
        results = [
            exercise(driver, db, "xwing"),
            exercise(driver, db, "scotts-macbook-air"),
        ]
    print(json.dumps({"mode": "neo4j-uncommitted-rollback-only", "results": results}, sort_keys=True))
    return 0 if all(r["ok"] for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
