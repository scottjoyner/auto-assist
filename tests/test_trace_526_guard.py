"""Fail-closed admission and profile-metadata tests for synthetic Neo4j 5.26.

No Docker, live graph, NAS, external API, or private trace access.
"""
import copy
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import bench_trace_526_driver as target


def synthetic_container():
    return {
        "Name":"/assistx-tracebench-526-20261008",
        "State":{"Running":True},
        "Config":{"Image":"neo4j:5.26-enterprise","Env":["NEO4J_AUTH=none"]},
        "HostConfig":{"NetworkMode":target.NET,"NanoCpus":1000000000,
                      "Memory":2200*1024*1024,
                      "PortBindings":{"7687/tcp":[{"HostIp":"127.0.0.1","HostPort":"17687"}]}},
        "Mounts":[{"Type":"volume"}],
        "NetworkSettings":{"Networks":{target.NET:{"IPAddress":"172.23.0.2"}}},
    }


def stub(monkeypatch,mutator=None):
    container=synthetic_container()
    network={"Internal":True}
    if mutator:mutator(container,network)
    def fake(*args):
        if args[0]=="inspect":return [container]
        if args[:2]==("network","inspect"):return [network]
        raise AssertionError("unexpected read")
    monkeypatch.setattr(target,"docker_json",fake)


def test_valid_internal_graph_target(monkeypatch):
    stub(monkeypatch)
    assert target.guarded_uri()=="bolt://172.23.0.2:7687"


@pytest.mark.parametrize("change",[
    lambda c,n:c.update(Name="/neo4j"),
    lambda c,n:c["Config"].update(Image="neo4j:5.23.0"),
    lambda c,n:c["State"].update(Running=False),
    lambda c,n:c["HostConfig"].update(NetworkMode="bridge"),
    lambda c,n:n.update(Internal=False),
    lambda c,n:c["HostConfig"].update(NanoCpus=2000000000),
    lambda c,n:c["HostConfig"].update(Memory=4000000000),
    lambda c,n:c["Mounts"].append({"Type":"bind","Source":"/nas","Destination":"/data"}),
    lambda c,n:c["Config"].update(Env=["NEO4J_AUTH=admin/secret"]),
    lambda c,n:c["NetworkSettings"]["Networks"][target.NET].update(IPAddress="172.17.0.2"),
    lambda c,n:c["HostConfig"].update(PortBindings={"7687/tcp":[{"HostIp":"0.0.0.0"}]}),
])
def test_unsafe_stage_target_denied(monkeypatch,change):
    stub(monkeypatch,change)
    with pytest.raises(RuntimeError,match="UNSAFE_BENCHMARK_CONTAINER|UNEXPECTED_PRIVATE_TARGET_ADDRESS"):
        target.guarded_uri()


def test_neo4j_526_camel_case_plan_metrics_are_not_lost():
    p={"operatorType":"ProduceResults","dbHits":3,"rows":1,
       "children":[{"operatorType":"NodeByLabelScan","dbHits":85001,"rows":85000,"children":[]}]}
    v=target.profile_tree(p)
    assert v["operator_count"]==2
    assert v["total_reported_db_hits"]==85004
    assert v["operator_nodes"][1]["operator"]=="NodeByLabelScan"
    assert v["operator_nodes"][1]["rows"]==85000
