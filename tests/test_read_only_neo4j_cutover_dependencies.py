"""Synthetic source-only tests for read-only Neo4j cutover dependency preflight."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SOURCE=Path(__file__).resolve().parents[1] / "scripts" / "read_only_neo4j_cutover_dependencies.py"
spec=importlib.util.spec_from_file_location("cutover_dependencies",SOURCE)
assert spec and spec.loader
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
IMAGE="sha256:"+"a"*64
UNIT="""[Service]
ExecStart=/usr/bin/socat TCP-LISTEN:7687,bind=100.64.1.2,reuseaddr,fork TCP:127.0.0.1:7687
Restart=always
"""
SOCKETS="LISTEN 0 128 100.64.1.2:7687 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:7688 0.0.0.0:*\n"
GRAPH={"production_decision":"HOLD","change_authorized":False,
       "off_loopback_publication_count":2,"network_attachment_count":3}
MOUNTS=[{"Destination":target,"Type":"bind","RW":True,"Source":"/private/secret/path"}
        for target in ("/data","/import","/logs")]


def client(name, uri, network="docker_default"):
    return {"Name":"/"+name,
            "Config":{"Env":[f"NEO4J_URI={uri}","NEO4J_USER=test-user",
                             "NEO4J_PASSWORD=DO_NOT_EXPOSE"]},
            "HostConfig":{"NetworkMode":network}}


def clients():
    return [
        client("assistx-api","bolt://host.docker.internal:7688"),
        client("assistx-worker","bolt://host.docker.internal:7688"),
        client("assistx-hermes-adapter","bolt://host.docker.internal:7688"),
        client("phonelog","bolt://127.0.0.1:7688",network="host"),
        {"Name":"/unrelated","Config":{"Env":["X=other"]},"HostConfig":{"NetworkMode":"bridge"}},
    ]


def check(**changes):
    inputs=dict(graph=GRAPH,containers=clients(),unit=UNIT,active="active\n",
                sockets=SOCKETS,mounts=MOUNTS)
    inputs.update(changes)
    return gate.assess(**inputs)


def test_live_like_synthetic_preflight_is_hold_without_secret_leak():
    d=check()
    assert d["production_decision"]=="HOLD"
    assert d["cutover_authorized"] is False
    assert d["dispatch_authorized"] is False
    assert d["read_only"] and d["no_mutations"]
    assert d["clients"]["graph_client_config_count"]==4
    assert d["clients"]["expected_assistx_gateway_clients"]==3
    assert d["clients"]["phonelog_host_network_loopback_configured"] is True
    assert d["clients"]["unknown_graph_client_count"]==0
    assert d["expected_persistent_graph_bind_mounts"] is True
    assert d["secondary_forwarder"]["service_active"] is True
    assert d["secondary_forwarder"]["listen_scope"]=="nonloopback"
    assert d["secondary_forwarder"]["destination_scope"]=="loopback"
    assert d["secondary_forwarder"]["destination_loopback_socket_observed"] is False
    assert "secondary_forwarder_destination_not_confirmed" in d["risk_flags"]
    assert "backup_restore_evidence_unverified" in d["risk_flags"]
    report=json.dumps(d)
    for word in ("DO_NOT_EXPOSE","test-user","secret/path","100.64.1.2","127.0.0.1"):
        assert word not in report


def test_host_network_phones_loopback_is_host_loopback():
    d=check()
    assert d["clients"]["phonelog_host_network_loopback_configured"] is True


@pytest.mark.parametrize("path",[
    "bolt://neo4j:7687",
    "bolt://host.docker.internal:7687",
    "bolt://127.0.0.1:7688",
    "neo4j://unverified.test:7688",
])
def test_wrong_assistx_gateway_endpoint_reports_hold(path):
    running=clients()
    running[0]=client("assistx-api",path)
    d=check(containers=running)
    assert d["clients"]["expected_assistx_gateway_clients"]==2
    assert "required_assistx_client_route_not_verified" in d["risk_flags"]
    assert d["production_decision"]=="HOLD"


def test_phone_container_bridge_mode_cannot_treat_loopback_as_host():
    running=clients()
    running[3]=client("phonelog","bolt://127.0.0.1:7688",network="bridge")
    d=check(containers=running)
    assert d["clients"]["phonelog_host_network_loopback_configured"] is False
    assert "phonelog_endpoint_network_mode_mismatch" in d["risk_flags"]


def test_unknown_client_and_missing_client_reported():
    running=clients()[:3]+[client("another","bolt://host.docker.internal:7688")]
    d=check(containers=running)
    assert d["clients"]["unknown_graph_client_count"]==1
    assert d["clients"]["missing_expected_clients"]==1
    assert "incomplete_graph_client_inventory" in d["risk_flags"]


@pytest.mark.parametrize("url",["bolt://bad:non-numeric","bolt://[bad","bolt://x:99999"])
def test_malformed_client_uri_fail_closed(url):
    running=clients()
    running[0]=client("assistx-api",url)
    with pytest.raises(gate.PreflightHold,match="endpoint_shape"):
        check(containers=running)


def test_duplicate_container_name_or_excess_inventory_refused():
    with pytest.raises(gate.PreflightHold,match="duplicate"):
        check(containers=clients()+[clients()[0]])
    with pytest.raises(gate.PreflightHold,match="unbounded"):
        check(containers=clients()*30)


def test_secondary_forward_loopback_destination_present_can_reduce_one_risk_only():
    socket=SOCKETS+"LISTEN 0 128 127.0.0.1:7687 0.0.0.0:*\n"
    d=check(sockets=socket)
    assert d["secondary_forwarder"]["destination_loopback_socket_observed"] is True
    assert "secondary_forwarder_destination_not_confirmed" not in d["risk_flags"]
    assert d["production_decision"]=="HOLD"


@pytest.mark.parametrize("unit",[
    "ExecStart=/usr/bin/socat TCP-LISTEN:7687,bind=100.64.1.2\n",
    "ExecStart=/usr/bin/socat TCP:127.0.0.1:7687\n",
    "ExecStart=/usr/bin/socat TCP-LISTEN:7687 TCP:127.0.0.1:7687\nExecStart=/usr/bin/socat TCP-LISTEN:7687 TCP:127.0.0.1:7687\n",
])
def test_partial_or_ambiguous_forwarder_unit_refuses_evidence(unit):
    with pytest.raises(gate.PreflightHold,match="forwarder"):
        check(unit=unit)


@pytest.mark.parametrize("data",[
    {"unit":"ExecStart=/usr/bin/socat TCP-LISTEN:7687,bind=127.0.0.1,reuseaddr,fork TCP:127.0.0.1:7687\n",
     "wanted":"loopback"},
    {"unit":"ExecStart=/usr/bin/socat TCP-LISTEN:7687,bind=0.0.0.0,reuseaddr,fork TCP:127.0.0.1:7687\n",
     "wanted":"wildcard"},
])
def test_forwarder_bind_scope_is_classified(data):
    d=check(unit=data["unit"])
    assert d["secondary_forwarder"]["listen_scope"]==data["wanted"]
    assert d["production_decision"]=="HOLD"


def test_forwarder_inactive_not_treated_as_healthy():
    d=check(active="inactive")
    assert d["secondary_forwarder"]["service_active"] is False
    assert "systemd_forwarder_state_unverified" in d["risk_flags"]
    assert d["secondary_forwarder"]["service_healthy_verified"] is False


@pytest.mark.parametrize("change",[
    "mount_missing","mount_extra","mount_wrong_type","mount_read_only",
])
def test_persistent_data_mount_changes_hold_without_mutation(change):
    mounts=deepcopy(MOUNTS)
    if change=="mount_missing":
        mounts.pop()
    elif change=="mount_extra":
        mounts.append({"Destination":"/tmp","Type":"bind","RW":True})
    elif change=="mount_wrong_type":
        mounts[0]["Type"]="volume"
    else:
        mounts[1]["RW"]=False
    d=check(mounts=mounts)
    assert d["expected_persistent_graph_bind_mounts"] is False
    assert "persistent_graph_mount_layout_unverified" in d["risk_flags"]


def test_upstream_gate_cannot_claim_go():
    with pytest.raises(gate.PreflightHold,match="upstream_ingress"):
        check(graph={"production_decision":"GO","change_authorized":True})


def test_main_missing_arguments_never_runs_real_inventory(monkeypatch):
    called=[]
    monkeypatch.setattr(gate,"collect",lambda x:called.append(x))
    with pytest.raises(SystemExit) as exc:
        gate.main([])
    assert exc.value.code==2
    assert called==[]


def test_exception_is_redacted(monkeypatch,capsys):
    def fail(_):
        raise RuntimeError("NEO4J_PASSWORD=DO_NOT_EXPOSE tcp://private.uri")
    monkeypatch.setattr(gate,"collect",fail)
    rc=gate.main(["--expected-image",IMAGE,"--json"])
    out=capsys.readouterr().out
    assert rc==3
    assert "DO_NOT_EXPOSE" not in out
    assert "private.uri" not in out
    assert json.loads(out)["production_decision"]=="HOLD"


def test_even_perfect_synthetic_dependency_evidence_never_authorizes_cutover():
    d=check(sockets=SOCKETS+"LISTEN 0 128 127.0.0.1:7687 0.0.0.0:*\n")
    assert d["cutover_authorized"] is False
    assert d["dispatch_authorized"] is False
    assert d["backup_restore_evidence_verified"] is False


def test_static_no_mutating_actions_or_arbitrary_target():
    text=SOURCE.read_text()
    assert "systemctl restart" not in text
    assert "docker run " not in text
    assert "docker exec " not in text
    assert "docker network connect" not in text
    assert "iptables -A" not in text
    assert "CREATE USER" not in text
    assert '["systemctl", "cat", FORWARDER]' in text
    assert '["docker", "inspect", "neo4j"]' in text


def test_no_live_routes_call_preflight():
    root=SOURCE.parents[1]
    for path in ("src/assistx/api.py","src/assistx/jobs.py",
                 "src/assistx/routers/review.py","src/assistx/cli.py"):
        assert "read_only_neo4j_cutover_dependencies" not in (root/path).read_text()
