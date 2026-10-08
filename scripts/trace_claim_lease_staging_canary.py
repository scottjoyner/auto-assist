#!/usr/bin/env python3
"""Rollback-only Neo4j + real FastAPI route + real worker proof-noop pilot.

Uses ephemeral signer, fixture node identity and in-process HTTP. Does NOT
exercise deployed AssistX API authentication or execute on remote nodes.
No committed Neo4j rows. Staging journals live in a TemporaryDirectory.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from neo4j import READ_ACCESS, GraphDatabase

from assistx import fleet_node_agent
from assistx.trace_claim_lease_api import build_claim_lease_router
from assistx.trace_execution_adapter import TraceReceiptStore

NODES = ("xwing", "scotts-macbook-air")
STAGE_TOKEN = "staging-only-fixture-node-token"


def exercise(driver, database: str, node_id: str, *, cancel: bool) -> dict:
    task_id = "staging-rollback-" + secrets.token_hex(12)
    claim_id = "staging-claim-" + secrets.token_hex(12)
    signer = Ed25519PrivateKey.generate()
    with tempfile.TemporaryDirectory(prefix="assistx-real-proof-staging-") as location:
        private = Path(location)
        private.chmod(0o700)
        signing_path = private / "issuer-private.pem"
        verify_path = private / "issuer-public.pem"
        signing_path.write_bytes(
            signer.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        signing_path.chmod(0o600)
        verify_path.write_bytes(
            signer.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        verify_path.chmod(0o600)
        audit = private / "audit"
        audit.mkdir(mode=0o700)

        with driver.session(database=database) as session:
            tx = session.begin_transaction()
            try:
                tx.run(
                    "CREATE (t:Task {id:$id, ticket_type:'trace_probe', "
                    "kind:'trace_probe', status:'READY', target_agent_id:$node, "
                    "required_capabilities:['trace-probe'], "
                    'payload_json:\'{"command_id":"probe.noop.v1"}\', '
                    "created_at_ts:timestamp()})",
                    id=task_id,
                    node=node_id,
                ).consume()

                class ReadTransaction:
                    def get_task(self, requested_id):
                        record = tx.run(
                            "MATCH (t:Task {id:$id}) RETURN t",
                            id=requested_id,
                        ).single()
                        return dict(record["t"]) if record else None

                def verify_node(n, token):
                    if n != node_id or token != STAGE_TOKEN:
                        raise HTTPException(status_code=403, detail="staging_node_denied")

                app = FastAPI()
                app.include_router(
                    build_claim_lease_router(
                        neo_factory=lambda: ReadTransaction(),
                        auth_dependency=lambda: "staging-fixture-user",
                        verify_node_identity=verify_node,
                    )
                )
                client = TestClient(app)
                history = []

                def http(method, url, *, data=None, headers=None, **kwargs):
                    assert method == "POST"
                    history.append(urlsplit(url).path)
                    path = urlsplit(url).path
                    if path.endswith("/claim"):
                        if headers is None or headers.get("X-Fleet-Node-Token") != STAGE_TOKEN:
                            return 403, {}
                        row = tx.run(
                            "MATCH (t:Task {id:$id}) WHERE t.status='READY' "
                            "AND t.target_agent_id=$node AND t.kind='trace_probe' "
                            "AND t.ticket_type='trace_probe' "
                            "SET t.status='CLAIMED', t.claimed_by=$node, "
                            "t.claim_id=$claim, t.execution_attempt=1, "
                            "t.lease_expires_at_ts=timestamp()+60000 RETURN t",
                            id=task_id,
                            node=node_id,
                            claim=claim_id,
                        ).single()
                        if not row:
                            return 409, {"claimed": False}
                        return 200, {"claimed": True, "claim_id": claim_id, "task": dict(row["t"])}
                    if path.endswith("/complete"):
                        tx.run(
                            "MATCH (t:Task {id:$id}) SET t.status=$status",
                            id=task_id,
                            status=data["status"],
                        ).consume()
                        return 200, {"ok": True}
                    if path.endswith("/heartbeat"):
                        return 200, {"ok": True}
                    if "/api/fleet/trace-execution/" in path:
                        if cancel and path.endswith("/claim-current-status"):
                            tx.run(
                                "MATCH (t:Task {id:$id}) SET t.status='CANCELLED'",
                                id=task_id,
                            ).consume()
                        response = client.post(
                            path,
                            json=data,
                            headers=headers or {},
                        )
                        return response.status_code, response.json()
                    raise AssertionError("unrecognized stage endpoint")

                env = {
                    "FLEET_NODE_ID": node_id,
                    "FLEET_TRACE_PROBE_ENABLED": "true",
                    "FLEET_TRACE_REAL_EXECUTION_ENABLED": "true",
                    "FLEET_NODE_AUTH_TOKEN": STAGE_TOKEN,
                    "FLEET_TRACE_ISSUER_ORIGIN": "https://assistx.staging.invalid",
                    "FLEET_TRACE_LEASE_VERIFIER_KEY_FILE": str(verify_path),
                    "FLEET_TRACE_EXECUTION_AUDIT_ROOT": str(audit),
                    "ASSISTX_TRACE_LEASE_ISSUER_ENABLED": "true",
                    "ASSISTX_TRACE_LEASE_SIGNING_KEY_FILE": str(signing_path),
                }
                prior = {name: os.environ.get(name) for name in env}
                prior_http = fleet_node_agent._http
                try:
                    os.environ.update(env)
                    fleet_node_agent._http = http
                    task = {
                        "id": task_id,
                        "target_agent_id": node_id,
                        "kind": "trace_probe",
                        "ticket_type": "trace_probe",
                        "required_capabilities": ["trace-probe"],
                    }
                    caps, _ = fleet_node_agent._detect_capabilities(None)
                    if "trace-probe" not in caps:
                        raise RuntimeError("staging_capability_not_admitted")
                    fleet_node_agent._claim_and_run(
                        assistx_url="https://assistx.staging.invalid",
                        router_url="not-used.invalid",
                        auth=("fixture-user", "fixture-password"),
                        node_id=node_id,
                        caps=caps,
                        task=task,
                        lmstudio_url=None,
                    )
                finally:
                    fleet_node_agent._http = prior_http
                    for name, value in prior.items():
                        if value is None:
                            os.environ.pop(name, None)
                        else:
                            os.environ[name] = value
                after = tx.run(
                    "MATCH (t:Task {id:$id}) RETURN t.status AS status",
                    id=task_id,
                ).single()
                stage_status = after["status"] if after else None
                if cancel:
                    records = 0 if not (audit / "journal.jsonl").exists() else -1
                    result = stage_status == "FAILED" and records == 0
                else:
                    state = TraceReceiptStore(str(audit), node_id=node_id).verify()
                    records = state["records"]
                    result = stage_status == "DONE" and records == 2
                evidence = {
                    "node_id": node_id,
                    "cancellation_injected": cancel,
                    "stage_status": stage_status,
                    "receipts": records,
                    "ok": result,
                    "endpoints_invoked": history,
                    "real_assistx_deployment": False,
                    "fixture_auth": True,
                    "executed_commands": 0,
                }
            finally:
                tx.rollback()
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            remaining = session.run(
                "MATCH (t:Task {id:$id}) RETURN count(t) AS n",
                id=task_id,
            ).single()["n"]
        evidence["rollback_confirmed"] = remaining == 0
        evidence["persisted_fixture_rows"] = remaining
        evidence["ok"] = evidence["ok"] and remaining == 0
        return evidence


def main() -> int:
    with GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        connection_timeout=5,
    ) as driver:
        results = [
            exercise(driver, os.getenv("NEO4J_DATABASE", "assistx"), node, cancel=stop)
            for node in NODES
            for stop in (False, True)
        ]
    print(
        json.dumps(
            {"mode": "real-neo4j-uncommitted-fastapi-worker", "checks": results, "ok": all(x["ok"] for x in results)},
            sort_keys=True,
        )
    )
    return 0 if all(x["ok"] for x in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
