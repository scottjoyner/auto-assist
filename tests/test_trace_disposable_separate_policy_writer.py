"""Offline guarded separation of broker and Raft-only policy writer."""
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from trace_disposable_task_broker import RemotePolicyAdmission
from trace_disposable_separate_policy_writer import Policy
from trace_etcd_quorum_fence_research import FenceRefused
from test_trace_joined_disposable_raft_broker import controller


def writer_policy():
    _, authority, _ = controller()
    p = object.__new__(Policy)
    p.quorum = authority
    p.genesis = "genesis-ack"
    p.owner = "gateway-1"
    p.term = 1
    p.token = "temporary-test-only-token-123456789"
    return p, authority


def req(operation, plan="get_task_read", digest="a" * 64):
    return {"operation_id": operation, "plan_id": plan,
            "request_digest": digest}


def test_writer_only_grants_reviewed_plan_and_pinned_epoch():
    p, authority = writer_policy()
    operation = "auth:" + "a" * 64
    token = p.admit(req(operation))
    assert token in authority.snapshot().document["pending"]
    with pytest.raises(FenceRefused, match="EXISTING_OPERATION"):
        p.admit(req(operation))
    with pytest.raises(FenceRefused, match="IDEMPOTENCY_KEY_PAYLOAD_CONFLICT"):
        p.admit(req(operation, digest="b" * 64))
    with pytest.raises(ValueError, match="UNRECOGNIZED_POLICY_REQUEST"):
        p.admit(req("auth:" + "b" * 64, plan="DELETE_GRAPH"))
    with pytest.raises(ValueError, match="UNRECOGNIZED_POLICY_REQUEST"):
        p.admit({**req("auth:" + "b" * 64), "raw_cypher": "DELETE"})
    p.owner = "stale"
    with pytest.raises(FenceRefused, match="STALE_RAFT_EPOCH_OR_OWNER"):
        p.admit(req("auth:" + "b" * 64))


class Reply:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        pass
    def read(self, *_args):
        return json.dumps(self.payload).encode()


def client():
    return RemotePolicyAdmission({
        "writer_endpoint": "http://assistx-broker-policy-deadbeef:8810",
        "writer_token": "disposable-pinned-writer-token-000001"})


def test_broker_sends_only_stable_opaque_identity_and_fixed_plan():
    with patch("trace_disposable_task_broker.urllib.request.urlopen",
               return_value=Reply({"reserved": True,
                                  "reservation_id": "a"*32})) as call:
        assert client().admit(
            "fixture-logical-request-001", "get_task_read",
            "fixture-task-abcd") == "a"*32
    request = call.call_args.args[0]
    envelope = json.loads(request.data)
    assert set(envelope) == {"plan_id", "operation_id", "request_digest"}
    assert envelope["operation_id"].startswith("auth:")
    assert envelope["plan_id"] == "get_task_read"
    assert len(envelope["request_digest"]) == 64
    assert request.get_header("Authorization").startswith("Bearer ")


@pytest.mark.parametrize("origin", [
    "http://assistx-broker-policy-deadbeef.evil.com:8810",
    "http://assistx-broker-policy-deadbeef:8810@evil.com",
    "http://assistx-broker-policy-deadbeef:2379",
    "http://assistx-broker-policy-deadbeef:8810/query",
    "http://assistx-broker-policy-deadbeef:8810#graph",
    "http://assistx-broker-policy-nothex:8810",
    "https://assistx-broker-policy-deadbeef:8810",
])
def test_lookalike_writer_destination_refused_without_token_disclosure(origin):
    with pytest.raises(ValueError, match="INVALID_DISPOSABLE_POLICY_WRITER"):
        RemotePolicyAdmission({
            "writer_endpoint": origin,
            "writer_token": "disposable-pinned-writer-token-000001"})


@pytest.mark.parametrize("response", [
    {"reserved": True},
    {"reserved": False, "reservation_id": "a"*32},
    {"reserved": True, "reservation_id": "bad"},
    {"reserved": True, "reservation_id": "a"*32, "arbitrary": True},
])
def test_untrusted_writer_receipt_cannot_trigger_graph_action(response):
    with patch("trace_disposable_task_broker.urllib.request.urlopen",
               return_value=Reply(response)):
        with pytest.raises(FenceRefused, match="POLICY_WRITER_UNTRUSTED"):
            client().admit("fixture-logical-request-001",
                           "get_task_read", "fixture-task-abcd")


def test_broker_denies_policy_service_network_failure_without_fallback():
    with patch("trace_disposable_task_broker.urllib.request.urlopen",
               side_effect=URLError("outage")) as call:
        with pytest.raises(FenceRefused, match="POLICY_WRITER_UNAVAILABLE"):
            client().admit("fixture-logical-request-001",
                           "get_task_read", "fixture-task-abcd")
    call.assert_called_once()


def test_broker_is_not_given_raw_KV_config_in_split_mode():
    harness = Path(__file__).with_name("probe_trace_isolated_task_broker.py")
    src = harness.read_text()
    assert '"writer_endpoint": "http://" + writer + ":8810"' in src
    assert '"writer_token": writer_token' in src
    assert "broker_no_raw_etcd_network_path" in src
    assert "WORKER_DIRECT_POLICY_BYPASS" in src
    assert 'assert_disposable(writer, {writer_net, raft_net})' in src
    assert 'if joined and not split:' in src
    assert '"independent_host_or_mtls_policy_auth": False' in src
    assert "ASSISTX_DISPOSABLE_WRITER_SPLIT" in src


def test_unauthenticated_disposable_writer_probe_and_graph_write_isolation():
    probe = Path(__file__).with_name(
        "trace_disposable_writer_unauth.py").read_text()
    physical = Path(__file__).with_name(
        "probe_trace_isolated_task_broker.py").read_text()
    assert "status == 401" in probe
    assert '"/v1/admit"' in probe
    assert "writer_no_direct_neo4j_path" in physical
    assert "WRITER_DIRECT_NEO4J_BYPASS" in physical
    assert "UNAUTHENTICATED_POLICY_WRITER_ACCEPTED" in physical
    assert 'for port in ("7687", "7474"):' in physical
