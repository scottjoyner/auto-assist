"""No Docker required: synthetic fail-closed preflight for physical probes."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_trace_physical_observer_526 as observer
import probe_trace_physical_admission_526 as admission


def _neo():
    return {
        "Name": "/" + observer.NAME,
        "Config": {"Image": observer.IMAGE, "Labels":{
            "assistx.research.physical":"20261009"},
            "Env":["NEO4J_AUTH=none"]},
        "State":{"Running":True},
        "HostConfig":{
            "NetworkMode":observer.NETWORK, "PortBindings":{},
            "Binds":None,"NanoCpus":1_000_000_000,"Memory":2_147_483_648,
        },
        "Mounts":[],
        "NetworkSettings":{"Networks":{observer.NETWORK:{"IPAddress":"172.23.0.2"}}},
    }


def _network():
    return {"Internal":True}


def _inspect_fixture(monkeypatch,container,network):
    def fake(*args):
        if args[:1]==("inspect",):
            return [deepcopy(container)]
        if args[:2]==("network","inspect"):
            return [deepcopy(network)]
        raise AssertionError("unexpected docker access")
    monkeypatch.setattr(observer,"_docker_inspect",fake)


def test_only_exact_disposable_network_and_label_can_supply_bolt_uri(monkeypatch):
    _inspect_fixture(monkeypatch,_neo(),_network())
    uri,provenance=observer.guarded_endpoint()
    assert uri=="bolt://172.23.0.2:7687"
    assert provenance["published_ports"] is False
    assert provenance["internal"] is True


@pytest.mark.parametrize("case",[
    "production_image","wrong_name","missing_label","exposed_port",
    "bind_mount","external_network","extra_network","wildcard_cpu",
    "oversized_memory","running_false","auth_enabled"
])
def test_production_or_unsafe_target_refused_before_bolt_connection(monkeypatch,case):
    container=_neo()
    network=_network()
    if case=="production_image":container["Config"]["Image"]="neo4j:latest"
    if case=="wrong_name":container["Name"]="/assistx-prod-neo4j"
    if case=="missing_label":container["Config"]["Labels"]={}
    if case=="exposed_port":container["HostConfig"]["PortBindings"]={"7687/tcp":[{"HostIp":"0.0.0.0","HostPort":"7687"}]}
    if case=="bind_mount":container["Mounts"]=[{"Type":"bind","Source":"/nas"}]
    if case=="external_network":network["Internal"]=False
    if case=="extra_network":container["NetworkSettings"]["Networks"]["production"]={"IPAddress":"10.0.0.10"}
    if case=="wildcard_cpu":container["HostConfig"]["NanoCpus"]=0
    if case=="oversized_memory":container["HostConfig"]["Memory"]=6_442_450_944
    if case=="running_false":container["State"]["Running"]=False
    if case=="auth_enabled":container["Config"]["Env"]=[]
    _inspect_fixture(monkeypatch,container,network)
    with pytest.raises(RuntimeError,match="UNSAFE_RESEARCH_TARGET"):
        observer.guarded_endpoint()


@pytest.mark.parametrize("n",[1,3,5,10])
def test_real_query_slot_reservation_denies_bounded_contender_waves(n):
    class Reserved:
        def __init__(self):self.calls=0
        def acquire(self,name):
            self.calls+=1
            return type("Decision",(),{"reason":"full"})()
    ledger=Reserved()
    result=admission._contend(ledger,n)
    assert result["admitted"]==0
    assert result["denied"]==n
    assert ledger.calls==n


def test_redis_target_requires_strict_throwaway_containment(monkeypatch):
    fake={
        "Name":"/"+admission.REDIS_NAME,
        "Config":{"Image":admission.REDIS_IMAGE,
                  "Labels":{"assistx.research.physical":"20261009"}},
        "HostConfig":{
            "NetworkMode":observer.NETWORK,"PortBindings":{},"Binds":None,
            "NanoCpus":250_000_000,"Memory":134_217_728,
        },
        "NetworkSettings":{"Networks":{observer.NETWORK:{}}},
        "State":{"Running":True},
        "Mounts":[],
    }
    monkeypatch.setattr(admission,"_exec",
        lambda *args:__import__("json").dumps([fake]))
    admission._verify_redis_target()
    fake["Config"]["Labels"].clear()
    with pytest.raises(RuntimeError,match="NOT_DISPOSABLE_REDIS"):
        admission._verify_redis_target()


def test_server_term_witness_never_automatically_signs_slot_release():
    from inspect import getsource
    from probe_trace_terminate_tx_526 import run
    source=getsource(run)
    assert "automatic_admission_release" in source
    assert 'report["automatic_admission_release"]=False' in source
    assert "acknowledge_remote_closure(" not in source


@pytest.mark.parametrize("remote,port",[
    ("127.0.0.1",7687),("10.0.0.5",7687),
    ("8.8.8.8",7687),("172.23.0.2",443),
])
def test_bolt_blackhole_refuses_non_disposable_upstream(remote,port):
    from probe_trace_bolt_blackhole_526 import LoopbackBoltBlackhole
    with pytest.raises(ValueError,match="NON_DISPOSABLE_UPSTREAM"):
        LoopbackBoltBlackhole(remote,port)


def test_bolt_blackhole_listens_only_on_local_loopback():
    from probe_trace_bolt_blackhole_526 import LoopbackBoltBlackhole
    with LoopbackBoltBlackhole("172.23.0.2",7687) as relay:
        assert relay.listening.getsockname()[0]=="127.0.0.1"
        assert relay.local_port>0
        relay.freeze.set()
        assert relay.freeze.is_set()
        assert relay.forwarded==[0,0]
