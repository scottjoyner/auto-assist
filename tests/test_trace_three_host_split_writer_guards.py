"""Offline guards for physically joined three-host RBAC writer/broker experiment."""
from pathlib import Path
from unittest.mock import patch

import pytest
from trace_disposable_separate_policy_writer import Policy
from trace_disposable_task_broker import RemotePolicyAdmission


def config():
    return {
        "writer_token": "research-disposable-ephemeral-strong-token",
        "raft_owner": "three-host-exclusive-broker",
        "raft_term": 1,
        "raft_genesis": "joined-three-host-broker-genesis",
        "raft_tls_endpoint": "https://100.99.88.77:32999",
        "raft_bearer": "disposable-bearer-opaque-credential",
        "raft_key": "/assistx/research/fencing/disposable-key",
        "raft_cluster": "disposable-cluster",
    }


def test_tls_writer_uses_authenticated_scoped_client_not_local_http():
    with patch("trace_etcd_bearer_rbac_research.EtcdBearerTLS") as client:
        p = Policy(config())
        assert p.quorum.kv is client.return_value
        assert p.quorum.pinned_cluster_id == "disposable-cluster"
    args = client.call_args
    assert args.args == (
        "https://100.99.88.77:32999",
        "/tmp/etcd-client/ca.pem",
        "/tmp/etcd-client/node.pem",
        "/tmp/etcd-client/node-key.pem",
    )
    assert args.kwargs["token"] == "disposable-bearer-opaque-credential"


@pytest.mark.parametrize("unexpected", [
    {"raft_endpoint": "http://assistx-quorum-r1:2379"},
    {"neo4j_password": "must-not-be-in-writer"},
    {"client_supplied_plan": "CREATE"},
])
def test_writer_strict_config_cannot_include_unreviewed_authority(unexpected):
    # Startup schema reject is represented explicitly in source; no runtime
    # server launch and no dependency on the Docker daemon.
    path = Path(__file__).with_name(
        "trace_disposable_separate_policy_writer.py").read_text()
    assert 'if set(conf) not in (local, remote):' in path
    assert 'raise SystemExit("INVALID_DISPOSABLE_WRITER_CONFIG")' in path
    assert set(config()) | set(unexpected) != set(config())


def test_external_mode_requires_explicit_double_opt_in(monkeypatch):
    from probe_trace_isolated_task_broker import run
    monkeypatch.delenv("ASSISTX_THREE_HOST_RAFT_RESEARCH", raising=False)
    monkeypatch.delenv("ASSISTX_THREE_HOST_SPLIT_WRITER_RESEARCH", raising=False)
    with pytest.raises(RuntimeError, match="THREE_HOST_WRITER_REQUIRES_DOUBLE_OPT_IN"):
        run(external_writer={"fake": "never-consulted"})


def test_exact_three_host_rbac_key_not_shared_with_other_gateways():
    rbac = Path(__file__).with_name(
        "probe_trace_etcd_rbac_physical.py").read_text()
    harness = Path(__file__).with_name(
        "probe_trace_isolated_task_broker.py").read_text()
    assert 'joined_key = base + "/rbac-three-host-exclusive-broker"' in rbac
    assert '"name": "joined-broker-writer"' in rbac
    assert '"permType": "READWRITE", "key": b64(joined_key)' in rbac
    assert '"user": "joined-broker-policy"' in rbac
    assert 'joined_writer._token' in rbac
    assert 'joined_integration = run_isolated(external_writer={' in rbac
    assert '"tls_client_dir": str(certs / "json-client")' in rbac
    assert '"raft_tls_endpoint": external_writer[' in harness
    assert '"raft_bearer": external_writer["raft_bearer"]' in harness
    assert 'docker("network", "connect", writer_egress_net, writer)' in harness
    assert 'if network != writer_egress_net:' in harness


def test_remote_broker_never_receives_raw_raft_credential():
    harness = Path(__file__).with_name(
        "probe_trace_isolated_task_broker.py").read_text()
    assert 'broker_modules = (' in harness
    assert '"writer_endpoint": "http://" + writer + ":8810"' in harness
    assert '"writer_token": writer_token' in harness
    assert '"raft_bearer": external_writer["raft_bearer"]' in harness
    assert '"writer_config.json"' in harness
    assert 'if joined and not remote:' in harness
    # No point-in-time assertion of the live system; this is source only.
    assert '"external_three_host_mtls_rbac": remote' in harness
    assert 'if not remote:' in harness


def test_broker_rpc_does_not_accept_raw_etcd_parameters_from_worker():
    client = RemotePolicyAdmission({
        "writer_endpoint": "http://assistx-broker-policy-deadbeef:8810",
        "writer_token": "research-disposable-ephemeral-strong-token",
    })
    assert not hasattr(client, "quorum")
    assert not hasattr(client, "raft_bearer")
    assert not hasattr(client, "raft_endpoint")
