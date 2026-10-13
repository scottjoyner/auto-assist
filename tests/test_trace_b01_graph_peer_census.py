"""Pure tests for fail-closed, secret-free B01 all-peer census."""
import json
from pathlib import Path
import pytest
from trace_b01_graph_peer_census import (
    analyze, inspect_projection, CensusDenied, PORTS, FMT)

def record(nets, published=None):
    return {"networks":list(nets), "ports":{p:(published or {}).get(p,[])
                                            for p in PORTS}}

def examples():
    return {"neo4j":record(["shared","graph"],{
                "7687/tcp":[{"HostIp":"0.0.0.0","HostPort":"7688"}],
                "7474/tcp":[{"HostIp":"0.0.0.0","HostPort":"7475"}]}),
            "assistx-api":record(["shared"]),
            "assistx-worker":record(["shared"]),
            "assistx-redis":record(["shared"]),
            "auto-assign":record(["shared"]),
            "assistx-hermes-adapter":record(["shared"]),
            "git-router-1":record(["shared"]),
            "unrelated":record(["unrelated"])}

def test_six_shared_peers_and_wildcard_exposure_fail_closed():
    result=analyze(examples())
    assert result["verdict"]=="NETWORK_BYPASS_PRESENT_NO_GO"
    assert result["potential_bypass_peer_count"]==6
    assert set(result["potential_bypass_peers"]) == {
        "assistx-api","assistx-worker","assistx-redis",
        "auto-assign","assistx-hermes-adapter","git-router-1"}
    assert result["wildcard_bindings"]=={
        "7687/tcp":True,"7474/tcp":True}
    assert not result["release_authorized"]
def test_missing_api_worker_graph_fails_closed():
    for name in ("neo4j","assistx-api","assistx-worker"):
        entries=examples()
        del entries[name]
        assert analyze(entries)["verdict"]=="INCOMPLETE_FAIL_CLOSED"

def test_empty_overlap_with_unpublished_ports_is_not_proof_of_isolation():
    entries=examples()
    entries["neo4j"]=record(["private"])
    report=analyze(entries)
    assert report["verdict"]=="ISOLATION_UNPROVEN"
    assert report["potential_bypass_peers"]=={}
    assert report["host_and_tailnet_firewall_trust_proven"] is False

def test_published_port_alone_is_a_no_go_even_without_network_overlap():
    entries=examples()
    entries["neo4j"]=record(["private"],{
        "7687/tcp":[{"HostIp":"127.0.0.1","HostPort":"7688"}]})
    assert analyze(entries)["verdict"]=="NETWORK_BYPASS_PRESENT_NO_GO"

def test_readonly_probe_does_not_query_secrets_or_graph():
    code=Path(__file__).with_name("trace_b01_graph_peer_census.py").read_text()
    for bad in ("Config.Env","NEO4J_PASSWORD","cypher-shell",
                "docker exec","network connect","network disconnect",
                "docker run", "GraphDatabase.driver"):
        assert bad not in code
    assert "ASSISTX_B01_CENSUS_READONLY" in code
    assert ".NetworkSettings.Networks" in FMT
def test_inspect_projection_uses_only_formatted_nonsecret_metadata(monkeypatch):
    import trace_b01_graph_peer_census as mod
    seen=[]
    def fake(*args):
        seen.append(args)
        return (json.dumps("/assistx-worker")+"<<<B01>>>"+
                json.dumps({"shared":{"IPAddress":"172.0.0.3"}})+"<<<B01>>>"+
                json.dumps(None))
    monkeypatch.setattr(mod,"_run",fake)
    result=inspect_projection("assistx-worker")
    assert result==record(["shared"])
    assert seen==[("inspect","--format",FMT,"assistx-worker")]

@pytest.mark.parametrize("name",[";rm -rf /", "../neo4j", "x y", ""])
def test_invalid_inspect_name_denied(name):
    with pytest.raises(CensusDenied):
        inspect_projection(name)

def test_invalid_projection_fails_closed(monkeypatch):
    import trace_b01_graph_peer_census as mod
    monkeypatch.setattr(mod,"_run",lambda *args: "{}")
    with pytest.raises(CensusDenied):
        inspect_projection("assistx-api")