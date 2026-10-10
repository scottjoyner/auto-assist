"""Offline contract guards for optional three-host RAFT + real Neo4j probe.

These never start Docker, Tailscale, SSH, certificates, network listeners,
or production services. Physical behavior is separately opt-in.
"""
import ast
from pathlib import Path

PHYSICAL = Path(__file__).with_name(
    "probe_trace_quorum_neo4j_physical.py").read_text()
ORCHESTRATOR = Path(__file__).with_name(
    "probe_trace_etcd_three_hosts.py").read_text()


def test_nested_explicit_optin_and_no_implicit_execution():
    assert 'ASSISTX_THREE_HOST_RAFT_RESEARCH' in ORCHESTRATOR
    assert 'ASSISTX_QUORUM_NEO4J_RESEARCH' in ORCHESTRATOR
    assert 'EXPLICIT_REAL_QUORUM_GRAPH_OPT_IN_REQUIRED' in PHYSICAL
    assert 'if os.getenv("ASSISTX_QUORUM_NEO4J_RESEARCH") == "1":' in ORCHESTRATOR
    assert "def run_real_quorum_graph(" in PHYSICAL
    ast.parse(PHYSICAL)


def test_graph_is_disposable_without_published_ports_or_database_mounts():
    assert 'NEO4J_IMAGE = "neo4j:5.26-enterprise"' in PHYSICAL
    assert '"--internal"' in PHYSICAL
    assert '"NEO4J_AUTH=none"' in PHYSICAL
    assert '"PortBindings"' in PHYSICAL
    assert 'get("Binds")' in PHYSICAL
    assert 'm["Type"] == "bind"' in PHYSICAL
    assert '"docker", "rm", "-f", "-v", graph_name' in PHYSICAL
    assert '"docker", "network", "rm", network' in PHYSICAL


def test_real_gateway_uses_same_authority_adapter_and_allowlist():
    assert 'QuorumPlanGrantAdapter(' in PHYSICAL
    assert 'ProtectedGraphEntry(' in PHYSICAL
    assert 'SqliteResearchJournal(' in PHYSICAL
    assert 'QueryPlan(' in PHYSICAL
    assert 'gateway_metadata(attempt, "approved_read")' in PHYSICAL
    assert 'tx.run(cypher, **dict(parameters))' in PHYSICAL


def test_quorum_loss_preserves_active_tx_and_prevents_another_query():
    assert '"xwing", "destroyer"' in PHYSICAL
    assert 'started.remove(host)' in PHYSICAL
    assert 'started.add(host)' in PHYSICAL
    assert 'graph.calls == 1' in PHYSICAL
    assert 'OLD_NEO4J_TRANSACTION_DID_NOT_SURVIVE_QUORUM_LOSS' in PHYSICAL
    assert 'PHYSICAL_CAPACITY_ALREADY_RELEASED' in PHYSICAL
    assert 'UNCERTAIN_PHYSICAL_WORK_BLOCKS_TAKEOVER' in PHYSICAL


def test_separate_signer_observes_real_transaction_and_rejects_active():
    assert 'ctx.Process(' in PHYSICAL
    assert 'target=_detached_witness' in PHYSICAL
    assert 'observed_exact(_transactions(driver), metadata)' in PHYSICAL
    assert 'channel.recv()["result"] == "still-active"' in PHYSICAL
    assert 'absent >= 2' in PHYSICAL
    assert 'quorum.close_with_witness' not in PHYSICAL
    assert 'coordinator.close_with_witness(' in PHYSICAL
    assert 'CLOSURE_RECEIPT_REPLAY_ACCEPTED' in PHYSICAL


def test_production_and_server_enforcement_explicitly_disclaimed():
    assert '"server_enforced_neo4j_fencing": False' in PHYSICAL
    assert '"witness_external_durable_custody": False' in PHYSICAL
    assert '"etcd_scoped_kv_rbac_enforced": False' in PHYSICAL
    assert '"graph_cluster_generation_witnessed": False' in PHYSICAL
    assert '"production_authority": False' in PHYSICAL


def test_authenticated_raw_kv_policy_bypass_explicitly_reproduced():
    assert 'raw-kv-policy-bypass-negative' in PHYSICAL
    assert '"mod_revision": str(raw_before.mod_revision)' in PHYSICAL
    assert '"forged-raw-kv-writer"' in PHYSICAL
    assert '"authenticated_raw_kv_policy_bypass_reproduced": True' in PHYSICAL
    assert '"etcd_scoped_kv_rbac_enforced": False' in PHYSICAL
