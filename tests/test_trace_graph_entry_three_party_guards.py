"""Offline structure/privilege guards for manual-only physical three-party probe.

No import or execution of the probe: hosted CI has no Docker or PG client.
"""
import ast
from pathlib import Path

SOURCE = Path(__file__).with_name("probe_trace_entry_three_party.py").read_text()
TREE = ast.parse(SOURCE)


def test_opt_in_gate_precedes_fixture_creation():
    run = next(node for node in TREE.body
               if isinstance(node, ast.FunctionDef) and node.name == "run")
    assert isinstance(run.body[0], ast.If)
    check = ast.get_source_segment(SOURCE, run.body[0])
    assert "ASSISTX_THREE_PARTY_PHYSICAL_RESEARCH" in check
    assert "EXPLICIT_THREE_PARTY_OPT_IN_REQUIRED" in check
    assert SOURCE.index('def run():') < SOURCE.index('docker("network", "create"')


def test_worker_network_credentials_and_mount_boundaries():
    assert '"--network", "none"' in SOURCE
    assert '"--cap-drop", "ALL"' in SOURCE
    assert '"--read-only"' in SOURCE
    assert '"--security-opt", "no-new-privileges"' in SOURCE
    assert '"--user", "65534:65534"' in SOURCE
    assert '"type=bind,source=" + str(ipc_home)' in SOURCE
    assert 'target=/ipc,readonly"' in SOURCE
    assert 'worker_mount_only_ipc_socket' in SOURCE
    assert 'len(mounts) != 1' in SOURCE


def test_three_separate_parties_and_no_gateway_release_method():
    assert 'target=_witness' in SOURCE
    assert 'args=(pg_verifier_dsn, real_uri, epoch, wchild, custody_path)' in SOURCE
    assert 'target=_gateway' in SOURCE
    assert 'args=(socket_path, pg_worker_dsn, relay_uri, epoch,' in SOURCE
    gateway_code = SOURCE.split('def _gateway(', 1)[1].split('def _append_custody(', 1)[0]
    assert 'release_exact(' not in gateway_code
    assert 'ProtectedGraphEntry(' in gateway_code
    assert 'GraphDatabase.driver(' in gateway_code


def test_observer_checks_exact_pg_reservation_and_real_graph_transaction():
    observer = SOURCE.split('def _witness(', 1)[1].split('def _fixture_ip(', 1)[0]
    assert 'assistx_trace_fence_research.verify_binding' in observer
    assert 'observed_exact(_snapshot(driver), metadata)' in observer
    assert 'absent >= 2' in observer
    assert 'wrong-token-or-unobserved' in observer
    assert 'pg-binding-denied' in observer


def test_fsync_before_release_and_custody_failure_holds_capacity():
    observer = SOURCE.split('def _witness(', 1)[1].split('def _fixture_ip(', 1)[0]
    assert observer.index('_append_custody(') < observer.index(
        'assistx_trace_fence_research.release_exact'
    )
    assert 'custody-unavailable' in observer
    assert 'os.fsync(fd)' in SOURCE
    assert '"/dev/full"' in SOURCE
    assert 'assert _pg_capacity(pg_worker_dsn, epoch) == 1' in SOURCE
    assert '"signed_append_only_custody": False' in SOURCE


def test_no_production_or_distributed_authority_claim():
    for fragment in [
        '"production_authority": False',
        '"pg_quorum_fencing_term": False',
        '"server_issued_neo4j_permit": False',
        '"distributed_failover_proven": False',
    ]:
        assert fragment in SOURCE
