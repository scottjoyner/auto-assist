"""Offline contract tests for the disposable isolated Task lookup broker.

These tests never launch containers or connect to a real graph instance.
"""
import json
from pathlib import Path

import pytest

from trace_disposable_task_broker import (
    CYPHER, GRAPH_ENDPOINT, MAX_REQUEST_BYTES, PLAN, RequestDenied,
    decode_task_response, validate,
)


def ok_document(task):
    return {
        "results": [{
            "columns": ["task"],
            "data": [] if task is None else [{"row": [task]}],
        }],
        "errors": [],
    }


def test_exact_static_read_plan_matches_existing_neo4jclient_lookup():
    assert PLAN == "get_task_read"
    assert CYPHER == "MATCH (t:Task {id: $id}) RETURN t { .id, .status } AS task"
    assert GRAPH_ENDPOINT == "http://neo4j:7474/db/neo4j/tx/commit"
    assert "DELETE" not in CYPHER
    assert "CREATE" not in CYPHER
    assert "CALL" not in CYPHER


def test_exact_request_shaped_task_lookup_allowed():
    assert validate({"plan_id": PLAN,
                     "parameters": {"id": "fixture-task-A1bc"}}) == (
        "fixture-task-A1bc")


@pytest.mark.parametrize("payload", [
    {"plan_id": PLAN, "parameters": {"id": "fixture-task-A1bc"},
     "cypher": "MATCH (n) DELETE n"},
    {"plan_id": "MATCH (n) DELETE n",
     "parameters": {"id": "fixture-task-A1bc"}},
    {"plan_id": PLAN, "parameters": {"id": "fixture-task-A1bc",
                                    "password": "leak"}},
    {"plan_id": PLAN, "parameters": {"id": "task-not-approved"}},
    {"plan_id": PLAN, "parameters": {"id": None}},
    {"plan_id": PLAN, "parameters": {"id": "fixture-task-A1bc' OR true"}},
    {"plan_id": PLAN, "parameters": ["fixture-task-A1bc"]},
    {"plan_id": PLAN},
    {},
    "MATCH (n) RETURN n",
])
def test_worker_cannot_supply_cypher_connection_or_extra_parameters(payload):
    with pytest.raises(RequestDenied):
        validate(payload)


def test_successful_result_projection_only():
    assert decode_task_response(ok_document({
        "id": "fixture-task-abcd", "status": "READY"
    })) == {"id": "fixture-task-abcd", "status": "READY"}
    assert decode_task_response(ok_document(None)) is None


@pytest.mark.parametrize("document", [
    {},
    {"results": [], "errors": []},
    {"results": [{"columns": ["task", "other"], "data": []}], "errors": []},
    {"results": [{"columns": ["task"], "data": [
        {"row": [{"id": "fixture-task-abcd", "status": "READY", "secret": "x"}]}
    ]}], "errors": []},
    {"results": [{"columns": ["task"], "data": [
        {"row": [{"id": "fixture-task-abcd", "status": 1}]}
    ]}], "errors": []},
    {"results": [{"columns": ["task"], "data": [
        {"row": [{"id": "fixture-task-abcd", "status": "READY"}]},
        {"row": [{"id": "fixture-task-efgh", "status": "READY"}]},
    ]}], "errors": []},
    {"results": [{"columns": ["task"], "data": []}],
     "errors": [{"code": "Security.Forbidden"}]},
])
def test_invalid_graph_response_never_becomes_success(document):
    with pytest.raises(RequestDenied):
        decode_task_response(document)


def test_resource_safety_network_topology_and_cleanup_guards():
    probe = Path(__file__).with_name("probe_trace_isolated_task_broker.py").read_text()
    assert "EXPLICIT_DISPOSABLE_GRAPH_BROKER_OPT_IN_REQUIRED" in probe
    assert 'docker("network", "connect", graph_net, broker)' in probe
    assert "assert_disposable(worker, {worker_net})" in probe
    assert "assert_disposable(graph, {graph_net})" in probe
    assert "assert_disposable(" in probe
    assert "broker, {graph_net, worker_net}," in probe
    assert "str(policy_socket_dir) if policy_socket_dir else None" in probe
    assert "expected_mount" in probe or "allowed_policy_mount" in probe
    assert "assert_disposable(worker, {worker_net})" in probe
    assert 'docker("rm", "-f", "-v", name' in probe
    assert 'docker("network", "rm", name' in probe
    assert '"NEO4J_AUTH=neo4j/" + admin' in probe
    assert "GRANT MATCH {id, status, payload, payload_json, queue_class" in probe
    assert "required_capabilities} ON GRAPH neo4j NODES Task" in probe
    assert "READ_ONLY_ROLE_WAS_ALLOWED_TO_WRITE" in probe
    assert "NEO4J_AUTH=none" not in probe
    assert "production_authority" in probe


def test_worker_code_never_contains_direct_neo4j_credentials():
    worker = Path(__file__).with_name(
        "trace_disposable_task_worker.py").read_text()
    assert "DIRECT_BOLT_BYPASS" in worker
    assert "DIRECT_GRAPH_HTTP_BYPASS" in worker
    assert '"Authorization": "Bearer "' in worker
    assert "NEO4J_PASSWORD" not in worker
    assert "neo4j_ip" in worker  # literal-IP denial, not a success route
    assert MAX_REQUEST_BYTES <= 4096


def test_server_discloses_no_unreviewed_cypher_path():
    broker = Path(__file__).with_name("trace_disposable_task_broker.py").read_text()
    assert 'self.path != "/v1/plan"' in broker
    assert '"UNKNOWN_STATIC_PLAN"' in broker
    assert '"UNAUTHORIZED"' in broker
    assert "GRAPH_ENDPOINT" in broker
    assert "params" in broker
    assert "self.headers.get" in broker
    assert "Raft" in broker or "raft" in broker
