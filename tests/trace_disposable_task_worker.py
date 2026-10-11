"""Disposable worker-only network check for isolated task-read broker.

Runs in a Python container with NO graph network attachment and no Neo4j
credential files. Only a fixture bearer token for the restricted broker.
"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import urllib.error
import urllib.request

from trace_task_read_broker_client_research import TaskReadBrokerClient

CONFIG = json.loads(Path("/tmp/worker-config.json").read_text())


def request(payload, bearer=True):
    req = urllib.request.Request(
        "http://" + CONFIG["broker_host"] + ":8808/v1/plan",
        data=json.dumps(payload).encode(), method="POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": "Bearer " + CONFIG["bearer"]} if bearer else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read(8192))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read(8192))


def socket_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def main():
    task_id = CONFIG["task_id"]
    task_client = TaskReadBrokerClient(
        "http://" + CONFIG["broker_host"] + ":8808", CONFIG["bearer"])
    # This is the actual Neo4jClient.get_task-shaped worker method canary,
    # returning an intentionally limited id/status subset from the fixture.
    assert task_client.get_task(task_id) == {
        "id": task_id, "status": "READY"}
    assert task_client.get_task("fixture-task-notpresent") is None
    critical = task_client.get_task_claim_guard(CONFIG["critical_task_id"])
    recovery = task_client.get_task_claim_guard(CONFIG["recovery_task_id"])
    assert critical["id"] == CONFIG["critical_task_id"]
    assert critical["queue_class"] == "critical"
    assert json.loads(critical["payload_json"])["queue_class"] == "critical"
    assert "recovery" in recovery["required_capabilities"]
    assert json.loads(recovery["payload_json"])["runbook"] == "fixture-only"
    payload_recovery = task_client.get_task_claim_guard(
        CONFIG["payload_recovery_task_id"])
    assert json.loads(payload_recovery["payload"])["runbook"] == "payload-only"
    assert payload_recovery["required_capabilities"] == []
    assert task_client.get_task(CONFIG["critical_task_id"]) == {
        "id": CONFIG["critical_task_id"], "status": "READY",
    }, "MINIMAL_PLAN_LEAKED_CLAIM_POLICY_FIELDS"
    valid = {"plan_id": "get_task_read", "parameters": {"id": task_id}}
    accepted_status, body = request(valid)
    assert accepted_status == 200, (accepted_status, body)
    assert body == {"task": {"id": task_id, "status": "READY"}}, body

    missing_status, missing = request(valid, bearer=False)
    assert missing_status == 401, missing
    assert missing == {"error": "UNAUTHORIZED"}

    arbitrary_status, arbitrary = request(
        {"plan_id": "MATCH (n) DELETE n", "parameters": {"id": task_id}})
    assert arbitrary_status == 422, arbitrary

    injected_status, injected = request({
        "plan_id": "get_task_read",
        "parameters": {"id": task_id, "cypher": "MATCH (n) DETACH DELETE n"},
    })
    assert injected_status == 422, injected

    missing_task, missing_body = request({
        "plan_id": "get_task_read",
        "parameters": {"id": "fixture-task-notpresent"},
    })
    assert missing_task == 200 and missing_body == {"task": None}

    # Use literal Neo4j IP, not just DNS: this is the crucial bypass denial.
    assert not socket_open(CONFIG["neo4j_ip"], 7687), "DIRECT_BOLT_BYPASS"
    assert not socket_open(CONFIG["neo4j_ip"], 7474), "DIRECT_GRAPH_HTTP_BYPASS"
    assert socket_open(CONFIG["broker_host"], 8808)

    print(json.dumps({
        "worker_restricted_task_lookup": True,
        "worker_get_task_client_adapter": True,
        "worker_claim_policy_projection_critical": True,
        "worker_claim_policy_projection_recovery": True,
        "worker_claim_policy_projection_payload_only_recovery": True,
        "minimal_projection_does_not_leak_policy_fields": True,
        "worker_response_projection": "Task.id,Task.status",
        "missing_broker_bearer_denied": True,
        "arbitrary_cypher_plan_denied": True,
        "injected_cypher_parameter_denied": True,
        "unknown_task_returns_none": True,
        "direct_bolt_literal_ip_denied": True,
        "direct_graph_http_literal_ip_denied": True,
        "broker_endpoint_accessible": True,
        "production_neo4j_or_api_touched": False,
        "raft_admission_integrated": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
