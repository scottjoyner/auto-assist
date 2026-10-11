"""Fail-closed tests for actual isolated broker Raft policy IPC ordering.

These are offline local Unix sockets. A separately opted-in physical test uses
real three-voter etcd and Neo4j. NEVER treat this suite as deployment evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import socketserver
import threading

import pytest

from trace_disposable_task_broker import (
    RequestDenied, reserve_via_quorum_policy_socket,
)


@contextmanager
def policy_socket(tmp_path, response):
    path = str(tmp_path / "policy.sock")
    messages = []
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            req = json.loads(self.rfile.readline())
            messages.append(req)
            self.wfile.write(json.dumps(response).encode() + b"\n")
    server = socketserver.UnixStreamServer(path, Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield path, messages
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def reserve(path, key="fixture-repeat-key-0001", plan="get_task_read",
            task="fixture-task-1234"):
    return reserve_via_quorum_policy_socket(
        path, "fixture-worker", key, plan, task)


def test_real_unix_socket_requires_strict_pinned_success_receipt(tmp_path):
    response = {"ok": {
        "reservation_id": "a" * 32, "term": 1,
        "epoch": "task-broker-raft-genesis",
    }}
    with policy_socket(tmp_path, response) as (path, seen):
        assert reserve(path) is None
        assert reserve(path) is None
    assert len(seen) == 2
    assert all(set(x) == {
        "kind", "plan_id", "operation_id", "request_digest"
    } for x in seen)
    assert seen[0] == seen[1]
    assert seen[0]["kind"] == "admit_stable"
    assert seen[0]["operation_id"].startswith("auth:")
    assert len(seen[0]["request_digest"]) == 64


def test_changed_plan_same_id_retains_operation_identity_changes_digest(tmp_path):
    response = {"ok": {
        "reservation_id": "a" * 32, "term": 1,
        "epoch": "task-broker-raft-genesis",
    }}
    with policy_socket(tmp_path, response) as (path, seen):
        reserve(path)
        reserve(path, plan="get_task_claim_guard")
    assert seen[0]["operation_id"] == seen[1]["operation_id"]
    assert seen[0]["request_digest"] != seen[1]["request_digest"]


@pytest.mark.parametrize("denied", [
    "EXISTING_OPERATION_REQUIRES_RECONCILIATION",
    "IDEMPOTENCY_KEY_PAYLOAD_CONFLICT",
    "CLOSED_OPERATION_REPLAY_DENIED",
])
def test_known_duplicate_or_payload_conflict_never_retries_graph(tmp_path, denied):
    with policy_socket(tmp_path, {"denied": denied}) as (path, _):
        with pytest.raises(RequestDenied, match="GRAPH_ADMISSION_DUPLICATE"):
            reserve(path)


@pytest.mark.parametrize("response", [
    {"ok": {"reservation_id": "x" * 32,
            "term": 1, "epoch": "task-broker-raft-genesis"}},
    {"ok": {"reservation_id": "a" * 32,
            "term": 1, "epoch": "different-genesis"}},
    {"ok": {"reservation_id": "a" * 32,
            "term": "1", "epoch": "task-broker-raft-genesis"}},
    {"ok": {"reservation_id": "a" * 32}},
    {"denied": "POLICY_QUORUM_UNAVAILABLE_OR_UNCERTAIN"},
    {},
])
def test_malformed_or_uncertain_writer_reply_blocks_graph(tmp_path, response):
    with policy_socket(tmp_path, response) as (path, _):
        with pytest.raises(RequestDenied, match="GRAPH_ADMISSION_UNAVAILABLE"):
            reserve(path)


def test_missing_policy_service_or_idempotency_key_fails_closed(tmp_path):
    with pytest.raises(RequestDenied, match="GRAPH_ADMISSION_UNAVAILABLE"):
        reserve(str(tmp_path / "missing.sock"))
    with pytest.raises(RequestDenied, match="GRAPH_ADMISSION_UNAVAILABLE"):
        reserve("whatever", key="bad")
    with pytest.raises(RequestDenied, match="GRAPH_ADMISSION_UNAVAILABLE"):
        reserve("whatever", key="x" * 200)


def test_broker_source_places_raft_reservation_before_graph_execution():
    src = Path(__file__).with_name("trace_disposable_task_broker.py").read_text()
    assert src.index("reserve_via_quorum_policy_socket(",
                     src.index("def do_POST(")) < src.index(
                         "task = fetch_task(", src.index("def do_POST("))
    assert "self.server.admission_socket" in src
    assert "ASSISTX_DISPOSABLE_REQUIRE_RAFT" in src
    assert '"GRAPH_ADMISSION_UNAVAILABLE"' in src


def test_optional_physical_join_is_explicit_and_never_production_default():
    source = Path(__file__).with_name("probe_trace_etcd_three_hosts.py").read_text()
    assert 'ASSISTX_BROKER_RAFT_RESEARCH' in source
    assert 'run_isolated_broker(' in source
    probe = Path(__file__).with_name("probe_trace_isolated_task_broker.py").read_text()
    assert "raft_authority is not None" in probe
    assert "allowed_uid=observe_disposable_peer_uid(" in probe
    assert "SO_PEERCRED" in probe
    assert "incomplete_closure_capacity_retained" in probe
    assert "production_authority" in probe
