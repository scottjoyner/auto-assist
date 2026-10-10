"""Static no-Docker guards for explicitly opted-in cross-host etcd fixture."""
import ast
from pathlib import Path

CODE = Path(__file__).with_name("probe_trace_etcd_three_hosts.py").read_text()
TREE = ast.parse(CODE)


def test_gate_and_no_implicit_execution():
    run = next(node for node in TREE.body
               if isinstance(node, ast.FunctionDef) and node.name == "run")
    assert isinstance(run.body[0], ast.If)
    gate = ast.get_source_segment(CODE, run.body[0])
    assert "ASSISTX_THREE_HOST_RAFT_RESEARCH" in gate
    assert "EXPLICIT_THREE_HOST_RAFT_OPT_IN_REQUIRED" in gate


def test_three_distinct_hosts_and_mtls_bound_to_tailnet_ip():
    for host in ["x1-370", "xwing", "destroyer"]:
        assert host in CODE
    assert '"--network", "host"' in CODE
    assert '"--client-cert-auth"' in CODE
    assert '"--peer-client-cert-auth"' in CODE
    assert '"--peer-trusted-ca-file"' in CODE
    assert '"--trusted-ca-file"' in CODE
    assert '"--read-only", "--cap-drop", "ALL"' in CODE
    assert '"--user", "1000:1000"' in CODE
    assert "tailscale" in CODE
    assert "https://" in CODE


def test_quorum_loss_and_real_raw_cas_negative_is_required():
    assert '"/v3/maintenance/status"' in CODE
    assert '"real_raft_leader_re_election_observed"' in CODE
    assert '"quorum_loss_read_denied"' in CODE
    assert '"quorum_loss_write_denied"' in CODE
    assert "clients[\"r1\"].txn({" in CODE
    assert "MINORITY_RAFT_TXN_WRITE_SUCCEEDED" in CODE
    assert "not clients[\"r1\"].range(canary_key).get(\"kvs\")" in CODE


def test_no_lease_based_release_or_fake_closure():
    assert '"pending-long-running-graph"' in CODE
    assert "UNCERTAIN_PHYSICAL_WORK_BLOCKS_TAKEOVER" in CODE
    assert '"graph_witness_integrated": False' in CODE
    assert '"app_term_independently_enforced_in_graph": False' in CODE
    assert '"multi_site_failure_domains": False' in CODE
    assert '"operator_auto_failover": False' in CODE
    assert '"production_authority": False' in CODE


def test_scoped_cleanup_and_no_system_changes():
    assert '"docker", "rm", "-f", "-v", name' in CODE
    assert 'shutil.rmtree(staging, ignore_errors=True)' in CODE
    assert 'if staging.exists():' in CODE
    assert 'strict' not in CODE.lower() or "StrictHostKeyChecking=yes" in CODE
    assert '"/home/scott/git"' in CODE
    assert "iptables" not in CODE
    assert "systemctl" not in CODE
    assert "docker network create" not in CODE
