"""Offline safety checks for P0 disposable Neo4j RBAC counterexample."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from neo4j.exceptions import Neo4jError

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location(
    "p0_disposable_role_runner",ROOT/"tests/probe_runtime_projection_broker_rbac_physical.py")
runner=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=runner
spec.loader.exec_module(runner)

def container(*,network="isolate",port_bindings=None,binds=None,mounts=None):
    return [{
        "HostConfig":{"PortBindings":port_bindings,"Binds":binds},
        "NetworkSettings":{"Networks":{network:{"IPAddress":"172.20.0.3"}}},
        "Mounts": mounts if mounts is not None else [
            {"Type":"volume","Destination":"/data","Name":"a"*64}],
    }]

def test_explicit_opt_in_precedes_any_docker_call(monkeypatch):
    monkeypatch.delenv(runner.OPT_IN,raising=False)
    with patch.object(runner,"docker",side_effect=AssertionError("DOCKER_CALLED")):
        with pytest.raises(RuntimeError,match="EXPLICIT_DISPOSABLE_OPT_IN_REQUIRED"):
            runner.run()

def test_disposable_container_has_no_host_port_or_persistent_bind():
    with patch.object(runner,"docker",return_value=json.dumps(container())):
        assert runner.assert_disposable_container("fixture","isolate")=="bolt://172.20.0.3:7687"

@pytest.mark.parametrize("unsafe",[
    container(network="wrong"),
    container(port_bindings={"7687/tcp":[{"HostPort":"7687"}]}),
    container(binds=["/prod/neo4j:/data"]),
    container(mounts=[{"Type":"bind","Destination":"/data","Source":"/prod"}]),
    container(mounts=[{"Type":"volume","Destination":"/data","Name":"named"}]),
    container(mounts=[{"Type":"volume","Destination":"/var/lib","Name":"a"*64}]),
])
def test_disposable_topology_fails_closed(unsafe):
    with patch.object(runner,"docker",return_value=json.dumps(unsafe)):
        with pytest.raises(RuntimeError):
            runner.assert_disposable_container("fixture","isolate")

def test_privilege_denial_must_be_real_server_error():
    with patch.object(runner,"query",return_value=[]):
        with pytest.raises(RuntimeError,match="UNAUTHORIZED_GRAPH_WRITE_SUCCEEDED"):
            runner.must_deny(None,"WRITE_CYPHER")
    with patch.object(runner,"query",side_effect=Neo4jError()):
        assert runner.must_deny(None,"WRITE_CYPHER") is True

def test_runner_has_no_production_graph_or_privileged_key_dependency():
    code=(ROOT/"tests/probe_runtime_projection_broker_rbac_physical.py").read_text()
    for forbidden in ("NEO4J_URI", "NEO4J_PASSWORD", "os.getenv(\"HOME\"",
        "assistx-api", "assistx-worker", "docker compose", "--publish",
        "RuntimeProjectionApprovalReceipt SET", "DROP DATABASE", "dbms.kill",
        "systemctl", "RuntimeProjectionApprovalReceipt DELETE"):
        assert forbidden not in code
    assert 'if os.environ.get(OPT_IN) != "1":' in code
    assert 'docker("rm", "-fv", container' in code
    assert 'docker("network", "rm", network' in code
    assert "compromised" in code.lower()
    assert '"resource_side_signature_validation_proven": False' in code
    assert '"production_admission_authorized": False' in code

def test_separate_database_privilege_is_not_equivalent_to_verifying_signatures():
    code=(ROOT/"tests/probe_runtime_projection_broker_rbac_physical.py").read_text()
    assert "UNSIGNED_ARBITRARY_DIGEST" in code
    assert "must_deny(" in code
    assert "fixture_p0_api" in code and "fixture_p0_worker" in code
    assert "fixture_p0_broker" in code
    assert "require_uniqueness_constraints" not in code
    # Production signed-apply verifier remains the source of this separate gate.
    production=(ROOT/"scripts/runtime_projection_signed_apply_gate.py").read_text()
    assert "Ed25519PublicKey" in production
    assert "require_uniqueness_constraints" in production


def test_physical_fixture_explicitly_exercises_replay_and_rollback_negatives():
    code=(ROOT/"tests/probe_runtime_projection_broker_rbac_physical.py").read_text()
    assert "duplicate_receipt_denied = must_deny" in code
    assert "duplicate_canonical_denied = must_deny" in code
    assert "SET s.generation=642" in code
    assert '"broker_generation_rollback_counterexample_reproduced": True' in code
    assert '"db_receipt_id_uniqueness_denied_replay": duplicate_receipt_denied' in code
    assert '"db_canonical_name_uniqueness_denied_duplicate": duplicate_canonical_denied' in code
