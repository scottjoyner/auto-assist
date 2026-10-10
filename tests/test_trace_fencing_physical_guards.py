"""Offline/AST checks for the manual physical two-primary negative probe.

No Docker, PostgreSQL client, privileged credentials or remote nodes required.
"""
import ast
from pathlib import Path

CODE = Path(__file__).with_name(
    "probe_trace_fencing_two_primaries.py"
).read_text()


def test_explicit_opt_in_precedes_docker_network_creation():
    tree = ast.parse(CODE)
    run = next(x for x in tree.body
               if isinstance(x, ast.FunctionDef) and x.name == "run")
    assert isinstance(run.body[0], ast.If)
    gate = ast.get_source_segment(CODE, run.body[0])
    assert "ASSISTX_TWO_PRIMARY_FENCE_RESEARCH" in gate
    assert "EXPLICIT_TWO_PRIMARY_OPT_IN_REQUIRED" in gate


def test_research_containers_have_no_published_ports_or_host_db_binds():
    assert '"--internal"' in CODE
    assert '"--pull", "never"' in CODE
    assert '"postgres:17-alpine"' in CODE
    assert '"PortBindings"' in CODE
    assert 'host.get("Binds")' in CODE
    assert 'any(m["Type"] == "bind"' in CODE
    assert '"rm", "-f", "-v"' in CODE
    assert '"network", "rm", network' in CODE


def test_two_pg_accepts_are_deliberate_counterexample():
    assert '"independent-primary-A"' in CODE
    assert '"independent-primary-B"' in CODE
    assert 'accepted_a[0] is True and accepted_b[0] is True' in CODE
    assert '"dual_pg_cap_one_overadmission_reproduced": True' in CODE
    assert '"copied_pg_disk_state_tested": False' in CODE


def test_controller_does_not_automatically_take_over():
    assert '"UNCERTAIN_INFLIGHT_TAKEOVER_BLOCKED"' in CODE
    assert '"PENDING_PHYSICAL_CAPACITY"' in CODE
    assert '"authority_restart_retained_uncertainty": True' in CODE
    assert '"quorum_failover_proven": False' in CODE
    assert '"neo4j_stale_effects_fenced": False' in CODE
    assert '"production_authority": False' in CODE
