"""Opt-in same-broker Bolt hold / separate graph observer research guards."""
from pathlib import Path
import pytest

from trace_b06_broker_held_bolt import FIELDS, held_task


def read(name):
    return Path(__file__).with_name(name).read_text()


def test_only_exact_b06_metadata_keys_admitted_without_starting_bolt():
    metadata = {
        "assistx_schema": "research-gateway-entry-v1",
        "assistx_operation_id": "auth:" + "a"*64,
        "assistx_attempt_id": "b06-" + "b"*32,
        "assistx_reservation_id": "b"*32,
        "assistx_epoch": "disposable-experimental-epoch",
        "assistx_term": 1,
        "assistx_plan_id": "get_task_read",
    }
    assert set(metadata) == FIELDS
    with pytest.raises(ValueError, match="B06_EXPLICIT_TRANSACTION_REFUSED"):
        held_task("fixture-task-abcd", "not-used", "not-used",
                  {**metadata, "custom_cypher": "DELETE n"})


def test_broker_must_admit_before_observable_bolt_hold():
    src = read("trace_disposable_task_broker.py")
    assert src.index("reserved_token = self.server.raft_admission.admit(") < (
        src.index("task = held_task("))
    assert 'self.headers.get("X-B06-Explicit-Bolt") == "1"' in src
    assert "self.server.b06_explicit" in src
    assert '"B06_TRANSACTION_UNCERTAIN"' in src
    assert 'close_with_witness(' not in src
    assert 'quorum.bind(' not in src


def test_observer_has_separate_neo4j_username_and_no_write_grant():
    src = read("probe_trace_isolated_task_broker.py")
    assert 'observer_user = "fixture_observer"' in src
    assert '"GRANT SHOW TRANSACTION (fixture_reader) ON DATABASE "' in src
    assert 'user=observer_user' in src
    assert "OBSERVER_GRAPH_WRITE_ALLOWED" in src
    assert 'assert_disposable(observer, {graph_net})' in src
    assert 'B06_INDEPENDENT_ACTIVE_NOT_OBSERVED' in src
    assert 'B06_OBSERVER_ABSENCE_FAILED' in src
    assert 'B06_CAPACITY_RELEASED_UNVERIFIED' in src


def test_transaction_metadata_owned_by_broker_and_worker_cannot_supply_it():
    broker = read("trace_disposable_task_broker.py")
    worker = read("trace_b06_worker_held_request.py")
    assert '"assistx_reservation_id": reserved_token' in broker
    assert '"assistx_epoch": self.server.b06_genesis' in broker
    assert '"assistx_operation_id": original.operation' in broker
    assert '"plan_id": "get_task_read"' in worker
    assert '"X-B06-Explicit-Bolt": "1"' in worker
    assert '"metadata"' not in worker


def test_observer_is_observation_only_never_sign_or_release():
    source = read("trace_b06_observer_disposable.py")
    assert "capture_active(" in source
    assert "observe_absence_only(" in source
    assert '"release_authorized": False' in source
    assert "close_with_witness(" not in source
    assert "Ed25519PrivateKey" not in source
    assert 'GRANT' not in source
    assert 'SHOW TRANSACTIONS YIELD transactionId, database, metaData' in source
