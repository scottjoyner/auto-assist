"""Fail-closed research probe launch and Docker provenance contracts.

No Neo4j/Docker service is contacted by this unit suite.
"""
from __future__ import annotations
import os

import pytest

import probe_trace_physical_lifecycle_526 as physical
import probe_trace_transport_blackhole_526 as blackhole


def safe_fixture():
    return ({
        "Name":"/" + physical.NAME,
        "State":{"Running":True},
        "Config":{"Image":"neo4j:5.26-enterprise",
                  "Env":["NEO4J_AUTH=none","NEO4J_ACCEPT_LICENSE_AGREEMENT=yes"]},
        "HostConfig":{"NetworkMode":physical.NETWORK,"NanoCpus":1_000_000_000,
                      "Memory":1_887_436_800, "PortBindings":{}},
        "NetworkSettings":{"Networks":{physical.NETWORK:{"IPAddress":"172.23.0.2"}}},
        "Mounts":[{"Type":"tmpfs"}],
    }, {"Internal":True})


@pytest.mark.parametrize("probe", [physical, blackhole])
def test_probes_refuse_any_run_without_explicit_opt_in(monkeypatch,probe):
    monkeypatch.delenv("ASSISTX_TRACE_DISPOSABLE_PHYSICAL_PROBE",raising=False)
    with pytest.raises(RuntimeError,match="EXPLICIT_OPT_IN_REQUIRED"):
        probe.run()


def test_checked_uri_requires_exact_isolated_container_and_network(monkeypatch):
    container, network = safe_fixture()
    monkeypatch.setattr(physical, "_inspect",
        lambda command,*args:[container] if command=="inspect" else [network])
    assert physical.checked_uri()=="bolt://172.23.0.2:7687"


@pytest.mark.parametrize("fault", [
    ("host-network",lambda c,n:c["HostConfig"].update(NetworkMode="host")),
    ("published-bolt",lambda c,n:c["HostConfig"].update(
        PortBindings={"7687/tcp":[{"HostPort":"7687"}]})),
    ("extra-network",lambda c,n:c["NetworkSettings"]["Networks"].update(
        bridge={"IPAddress":"172.17.0.8"})),
    ("external-network",lambda c,n:n.update(Internal=False)),
    ("foreign-image",lambda c,n:c["Config"].update(Image="neo4j:latest")),
    ("not-running",lambda c,n:c["State"].update(Running=False)),
    ("uncapped-memory",lambda c,n:c["HostConfig"].update(Memory=0)),
    ("cpu-over-limit",lambda c,n:c["HostConfig"].update(NanoCpus=4_000_000_000)),
    ("host-mount",lambda c,n:c["Mounts"].append({"Type":"bind"})),
    ("wrong-ip",lambda c,n:c["NetworkSettings"]["Networks"][physical.NETWORK].update(
        IPAddress="10.0.0.10")),
    ("auth-enabled",lambda c,n:c["Config"].update(Env=["NEO4J_AUTH=example"])) ,
])
def test_physical_probe_refuses_unsafe_docker_target(monkeypatch,fault):
    import copy
    c,n=copy.deepcopy(safe_fixture())
    fault[1](c,n)
    monkeypatch.setattr(physical,"_inspect",
        lambda command,*args:[c] if command=="inspect" else [n])
    with pytest.raises(RuntimeError):
        physical.checked_uri()


def test_blackhole_proxy_listens_on_explicit_loopback():
    proxy=blackhole._Proxy(("127.0.0.1",0),"127.0.0.1")
    try:
        assert proxy.server_address[0]=="127.0.0.1"
        assert proxy.server_address[1] > 0
    finally:
        proxy.server_close()
