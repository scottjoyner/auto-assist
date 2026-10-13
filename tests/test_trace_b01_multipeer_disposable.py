"""Synthetic multi-peer probe safety and fail-closed behavior."""
import pytest
from trace_b01_multipeer_disposable import (
    ACTOR_ROLES, ALL_ROLE_NAMES, SOCKET_CHECK, verify_results)

def sample(**updates):
    default=dict(graph_ip_bolt=False,graph_ip_http=False,
                 graph_dns_bolt=False,graph_dns_http=False,
                 broker_tcp=True)
    default.update(updates)
    return default

def test_expected_seven_currently_shared_role_labels():
    assert ALL_ROLE_NAMES==frozenset((
        "assistx-api","assistx-worker","assistx-hermes-adapter",
        "assistx-redis","auto-assign","git-redis-1","git-router-1"))
    assert len(ACTOR_ROLES)==6
    assert verify_results(sample()) is True

@pytest.mark.parametrize("case",[
    {"graph_ip_bolt":True},{"graph_ip_http":True},
    {"graph_dns_bolt":True},{"graph_dns_http":True},
    {"broker_tcp":False},{"broker_tcp":None},
])
def test_any_bypass_or_broker_denial_fails_closed(case):
    with pytest.raises(RuntimeError):
        verify_results(sample(**case))
def test_incomplete_or_surprising_result_rejected():
    for bad in ({},{"graph_ip_bolt":False},dict(sample(),extra=True)):
        with pytest.raises(RuntimeError):
            verify_results(bad)

def test_raw_ip_and_dns_bolt_http_all_checked():
    for check in ("graph_ip_bolt","graph_ip_http",
                  "graph_dns_bolt","graph_dns_http","broker_tcp"):
        assert check in SOCKET_CHECK

def test_probe_attaches_only_on_worker_bridge():
    from pathlib import Path
    src=Path(__file__).with_name("trace_b01_multipeer_disposable.py").read_text()
    assert '"--network",worker_net' in src
    assert "assert_disposable(name,{worker_net})" in src
    assert 'docker("start",name)' in src
    assert "NEO4J_AUTH" not in src
    assert "GraphDatabase.driver" not in src