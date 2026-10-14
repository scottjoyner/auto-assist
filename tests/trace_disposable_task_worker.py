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


def request(payload, bearer=True, *, node_token=None):
    req = urllib.request.Request(
        "http://" + CONFIG["broker_host"] + ":8808/v1/plan",
        data=json.dumps(payload).encode(), method="POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": "Bearer " + CONFIG["bearer"]} if bearer else {}),
            **({"X-Research-Node-Token": node_token}
               if node_token is not None else {}),
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

    # The research classifier is only a fixed read plan, never a claim.
    policy_plan = "get_task_policy_classification"
    agent_id = CONFIG["policy_agent_id"]
    token = CONFIG["policy_agent_token"]
    def policy_request(tid, **params):
        return {"plan_id":policy_plan,
                "parameters":{"id":tid,"agent_id":agent_id,**params}}

    status, envelope = request(
        policy_request(CONFIG["recovery_task_id"]), node_token=token)
    assert status == 200, (status, envelope)
    policy = envelope["policy"]
    assert set(envelope) == {"policy"}
    assert policy["requires_recovery_node_identity"] is True
    assert policy["claim_authorized"] is False
    assert policy["resource_fence_verified"] is False
    assert policy["release_authorized"] is False
    assert set(policy) == {
        "schema","id","status","requires_recovery_node_identity",
        "drain_mode_policy_candidate","claim_authorized",
        "resource_fence_verified","release_authorized"}
    assert "runbook" not in json.dumps(envelope)
    assert "payload" not in json.dumps(envelope)

    missing_status, missing_envelope = request(
        policy_request(CONFIG["recovery_task_id"]))
    assert (missing_status, missing_envelope) == (
        403, {"error":"NODE_IDENTITY_REQUIRED"})
    wrong_status, wrong_envelope = request(
        policy_request(CONFIG["recovery_task_id"]),
        node_token="synthetic-incorrect-credential")
    assert (wrong_status, wrong_envelope) == (
        403, {"error":"NODE_IDENTITY_REQUIRED"})
    foreign_status, foreign_envelope = request(
        policy_request(CONFIG["recovery_task_id"],
                       agent_id="fixture-agent-unapproved"),
        node_token=token)
    assert (foreign_status, foreign_envelope) == (
        403, {"error":"NODE_IDENTITY_REQUIRED"})
    missing_bearer_status, _ = request(
        policy_request(CONFIG["recovery_task_id"]),
        node_token=token, bearer=False)
    assert missing_bearer_status == 401

    unknown_status, unknown_envelope = request(
        policy_request("fixture-task-nonexistent"), node_token=token)
    assert (unknown_status, unknown_envelope) == (200, {"policy":None})
    critical_status, critical_envelope = request(
        policy_request(CONFIG["critical_task_id"]), node_token=token)
    assert critical_status == 200
    assert critical_envelope["policy"]["drain_mode_policy_candidate"] is True
    assert critical_envelope["policy"]["requires_recovery_node_identity"] is False
    assert critical_envelope["policy"]["claim_authorized"] is False

    bad_status, bad_envelope = request(
        policy_request(CONFIG["malformed_policy_task_id"]), node_token=token)
    assert (bad_status, bad_envelope) == (
        503, {"error":"POLICY_OR_GRAPH_UNAVAILABLE"})

    extra_status, extra_envelope = request({
        "plan_id":policy_plan,
        "parameters":{"id":CONFIG["recovery_task_id"],
                      "agent_id":agent_id,"query":"MATCH (n) RETURN n"}},
        node_token=token)
    assert extra_status == 422
    assert extra_envelope == {"error":"INVALID_PLAN_OR_PARAMETERS"}

    # Use literal Neo4j IP, not just DNS: this is the crucial bypass denial.
    assert not socket_open(CONFIG["neo4j_ip"], 7687), "DIRECT_BOLT_BYPASS"
    assert not socket_open(CONFIG["neo4j_ip"], 7474), "DIRECT_GRAPH_HTTP_BYPASS"
    assert socket_open(CONFIG["broker_host"], 8808)

    print(json.dumps({
        "worker_restricted_task_lookup": True,
        "policy_http_recovery_success": True,
        "policy_http_missing_node_denied": True,
        "policy_http_bad_node_denied": True,
        "policy_http_wrong_agent_denied": True,
        "policy_http_bearer_denied": True,
        "policy_http_privacy_pass": True,
        "policy_http_malformed_graph_denied": True,
        "policy_http_never_claims_or_fences": True,
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