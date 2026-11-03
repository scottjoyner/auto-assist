"""Static and synthetic read-only Docker ingress research gate tests."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


PATH = Path(__file__).resolve().parents[1] / "scripts" / "read_only_neo4j_ingress_gate.py"
SPEC = importlib.util.spec_from_file_location("research_neo4j_ingress_gate", PATH)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

IMAGE = "sha256:" + "a"*64
IDENTITY = "b"*64


def make_snapshot(*, bind="127.0.0.1", network_count=1, image=IMAGE):
    ports = {
        "7474/tcp": [{"HostIp":bind,"HostPort":"7474"}],
        "7687/tcp": [{"HostIp":bind,"HostPort":"7687"}],
    }
    return {
        "Name":"/neo4j", "Id":IDENTITY, "Image":image,
        "State":{"Running":True,"StartedAt":"2026-11-03T18:00:00Z"},
        "Config":{"Labels":{
            "com.docker.compose.project":"docker",
            "com.docker.compose.service":"neo4j",
        }},
        "HostConfig":{"PortBindings":deepcopy(ports)},
        "NetworkSettings":{
            "Ports":deepcopy(ports),
            "Networks":{f"test-net-{i}":{} for i in range(network_count)},
        },
    }


def assessment(**kwargs):
    return gate.assess(make_snapshot(**kwargs),IMAGE)


def test_never_claims_security_go_even_for_loopback_one_network():
    result=assessment()
    assert result["production_decision"]=="HOLD"
    assert result["expected_loopback_port_publication"] is True
    assert result["external_reachability_verified"] is False
    assert result["physical_firewall_policy_verified"] is False
    assert result["change_authorized"] is False
    assert result["dispatch_authorized"] is False
    assert result["no_mutations"] is True
    assert "reconciled_production_container_identity_not_active" in result["risk_flags"]
    assert "host_and_upstream_firewall_reachability_unverified" in result["risk_flags"]


@pytest.mark.parametrize("binding",["0.0.0.0","::","", "192.168.1.12","2001:db8::1","garbage"])
def test_any_non_loopback_or_untrusted_address_fails_safe(binding):
    result=assessment(bind=binding)
    assert result["expected_loopback_port_publication"] is False
    assert result["off_loopback_publication_count"]==2
    assert "wildcard_or_nonloopback_graph_publication" in result["risk_flags"]
    assert result["production_decision"]=="HOLD"


@pytest.mark.parametrize("binding",["127.0.0.1","::1"])
def test_explicit_ipv4_ipv6_loopback_classified_as_loopback(binding):
    result=assessment(bind=binding)
    assert result["off_loopback_publication_count"]==0
    assert result["expected_loopback_port_publication"] is True
    assert result["production_decision"]=="HOLD"


def test_configured_runtime_publications_disagree_closed():
    snap=make_snapshot()
    snap["NetworkSettings"]["Ports"]["7687/tcp"].append(
        {"HostIp":"::","HostPort":"7687"})
    result=gate.assess(snap,IMAGE)
    assert result["expected_loopback_port_publication"] is False
    assert result["published_runtime_binding_mismatch"] is True
    assert "published_vs_runtime_binding_discrepancy" in result["risk_flags"]


def test_additional_networks_reported_without_raw_network_names():
    result=assessment(network_count=3)
    assert result["network_attachment_count"]==3
    assert "unexpected_number_of_network_attachments" in result["risk_flags"]
    assert "test-net-" not in json.dumps(result)


@pytest.mark.parametrize("variant",["extra","missing","duplicate"])
def test_extra_missing_or_duplicate_host_publication_is_denied(variant):
    snap=make_snapshot()
    pb=snap["HostConfig"]["PortBindings"]
    if variant=="extra":
        pb["17687/tcp"]=[{"HostIp":"127.0.0.1","HostPort":"17687"}]
    elif variant=="missing":
        del pb["7687/tcp"]
    else:
        pb["7687/tcp"].append({"HostIp":"127.0.0.1","HostPort":"17687"})
    result=gate.assess(snap,IMAGE)
    assert result["expected_loopback_port_publication"] is False
    assert "publication_ports_do_not_match_expected_two_single_bindings" in result["risk_flags"]


@pytest.mark.parametrize("bad",["not-a-digest",None,"sha256:"+"z"*64,"sha256:"+"a"*63])
def test_image_pin_invalid_before_docker(monkeypatch,bad):
    called=[]
    monkeypatch.setattr(gate,"_docker_inspect",lambda:called.append("used"))
    with pytest.raises(gate.IngressHold,match="immutable_sha256"):
        gate.collect(bad)
    assert called==[]


def test_actual_identity_mismatch_or_stopped_container_denied():
    snap=make_snapshot(image="sha256:"+"c"*64)
    with pytest.raises(gate.IngressHold,match="identity_or_image"):
        gate.assess(snap,IMAGE)
    snap=make_snapshot()
    snap["State"]["Running"]=False
    with pytest.raises(gate.IngressHold,match="identity_or_image"):
        gate.assess(snap,IMAGE)


@pytest.mark.parametrize("kind",["port_map_none","binding_nonlist","invalid_port","bad_networks"])
def test_malformed_docker_metadata_not_used_as_safe_evidence(kind):
    snap=make_snapshot()
    if kind=="port_map_none":
        snap["HostConfig"]["PortBindings"]=None
    elif kind=="binding_nonlist":
        snap["HostConfig"]["PortBindings"]["7687/tcp"]={"HostIp":"127.0.0.1"}
    elif kind=="invalid_port":
        snap["HostConfig"]["PortBindings"]["7687/tcp"][0]["HostPort"]="0"
    else:
        snap["NetworkSettings"]["Networks"]=[]
    with pytest.raises(gate.IngressHold):
        gate.assess(snap,IMAGE)


def test_unchanged_container_double_snapshot_required(monkeypatch):
    count=[]
    def inspect():
        count.append(1)
        return make_snapshot()
    monkeypatch.setattr(gate,"_docker_inspect",inspect)
    assert gate.collect(IMAGE)["production_decision"]=="HOLD"
    assert len(count)==2


@pytest.mark.parametrize("change",["id","startup","binding","networks"])
def test_race_or_restart_between_inspects_denied(monkeypatch,change):
    before=make_snapshot()
    after=deepcopy(before)
    if change=="id":
        after["Id"]="c"*64
    elif change=="startup":
        after["State"]["StartedAt"]="2026-11-03T19:00:00Z"
    elif change=="binding":
        after["HostConfig"]["PortBindings"]["7687/tcp"][0]["HostIp"]="0.0.0.0"
    else:
        after["NetworkSettings"]["Networks"]["another"]={}
    snapshots=iter((before,after))
    monkeypatch.setattr(gate,"_docker_inspect",lambda:next(snapshots))
    with pytest.raises(gate.IngressHold):
        gate.collect(IMAGE)


def test_docker_readback_never_runs_shell_and_redacts_error(monkeypatch,capsys):
    calls=[]
    def fail(args,**kwargs):
        calls.append((args,kwargs))
        return subprocess.CompletedProcess(args,1,stdout="",stderr="TOKEN=UNSAFE")
    monkeypatch.setattr(gate.subprocess,"run",fail)
    assert gate.main(["--expected-image",IMAGE,"--json"])==3
    result=json.loads(capsys.readouterr().out)
    assert result["production_decision"]=="HOLD"
    assert "TOKEN" not in str(result)
    assert result["dispatch_authorized"] is False
    assert calls[0][0]==["docker","container","inspect","neo4j"]
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["timeout"]==8


def test_main_with_good_synthetic_loopback_still_exit_hold(monkeypatch,capsys):
    monkeypatch.setattr(gate,"_docker_inspect",lambda:make_snapshot())
    assert gate.main(["--expected-image",IMAGE,"--json"])==3
    report=json.loads(capsys.readouterr().out)
    assert report["expected_loopback_port_publication"] is True
    assert report["production_decision"]=="HOLD"


def test_missing_required_arguments_never_inspects(monkeypatch):
    calls=[]
    monkeypatch.setattr(gate,"_docker_inspect",lambda:calls.append(1))
    with pytest.raises(SystemExit) as err:
        gate.main([])
    assert err.value.code==2
    assert calls==[]


def test_only_static_docker_inspection_not_network_probe_or_mutation():
    source=PATH.read_text()
    for forbidden in (
        "docker run","docker exec","docker stop","docker restart",
        "docker network connect","docker network disconnect",
        "subprocess.Popen(","curl ","nc ","nmap ","iptables -A",
        "nft add","GRANT WRITE","MATCH (t:Task)",
    ):
        assert forbidden not in source
    assert '"container", "inspect", TARGET' in source
    for module in ("src/assistx/api.py","src/assistx/jobs.py","src/assistx/routers/review.py"):
        assert "read_only_neo4j_ingress_gate" not in (PATH.parents[1]/module).read_text()


def test_reconciliation_overlay_is_not_the_legacy_wildcard_default():
    root=PATH.parents[1]
    overlay=(root/"compose.production.reconciled.yml").read_text()
    base=(root/"docker-compose.yml").read_text()
    assert "container_name: assistx-production-neo4j" in overlay
    assert "127.0.0.1:" in overlay
    assert "7474}:7474" in overlay
    assert "7687}:7687" in overlay
    assert "ASSISTX_NEO4J_BIND:-0.0.0.0" in base
    assert gate.EXPECTED_OVERLAY_NAME=="/assistx-production-neo4j"
