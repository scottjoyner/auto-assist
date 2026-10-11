"""Offline release guards for real three-host joined broker minority fault gate."""
from pathlib import Path


def test_three_host_fault_targets_only_disposable_voters_and_restores():
    source = Path(__file__).with_name(
        "probe_trace_isolated_task_broker.py").read_text()
    assert 'for host in ("xwing", "destroyer"):' in source
    assert 'if not name.startswith("assistx-raft-"):' in source
    assert 'remote_shell(' in source
    assert '["docker", "stop", "--time", "2", name]' in source
    assert 'finally:' in source
    assert '["docker", "start", name]' in source
    assert 'THREE_HOST_JOINED_MINORITY_NOT_DENIED' in source
    assert 'THREE_HOST_JOINED_RAFT_RECOVERY_FAILED' in source
    assert 'external_writer.get("fault_context") is not None' in source


def test_writer_cannot_read_foreign_rbac_key():
    rbac = Path(__file__).with_name(
        "probe_trace_etcd_rbac_physical.py").read_text()
    assert 'joined_writer.range(service_key)' in rbac
    assert 'JOINED_WRITER_ESCAPED_EXACT_KEY_RBAC' in rbac
    assert 'joined_key = base + "/rbac-three-host-exclusive-broker"' in rbac


def test_same_physical_broker_namespace_denies_remote_etcd():
    code = Path(__file__).with_name(
        "probe_trace_isolated_task_broker.py").read_text()
    assert 'BROKER_DIRECT_ETCD_BYPASS' in code
    assert 'WORKER_DIRECT_POLICY_BYPASS' in code
    assert 'WRITER_DIRECT_NEO4J_BYPASS' in code
    assert 'UNAUTHENTICATED_POLICY_WRITER_ACCEPTED' in code
    assert '"external_three_host_mtls_rbac": remote' in code
    assert '"broker_to_writer_mtls": False' in code
    assert '"distinct_writer_os_identity_verified": False' in code
