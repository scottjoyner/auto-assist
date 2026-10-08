"""No-network/no-real-data admission tests for the isolated synthetic benchmark."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import bench_trace_global_neo4j as bench


def isolated():
    return {
        "Name":"/assistx-tracebench-stage-20261008",
        "HostConfig":{
            "NetworkMode":"none","PortBindings":{},
            "NanoCpus":1000000000,"Memory":2200*1024*1024,
        },
        "Mounts":[{"Type":"volume"}],
        "Config":{"Image":"neo4j:5.23.0","Env":["NEO4J_AUTH=none"]},
        "State":{"Running":True},
    }


def verify(monkeypatch,data,rc=0):
    calls=[]
    def fake_run(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=rc,stdout=json.dumps([data]) if not rc else "")
    monkeypatch.setattr(bench.subprocess,"run",fake_run)
    if rc:
        with pytest.raises(RuntimeError,match="DISPOSABLE_CONTAINER_NOT_FOUND"):
            bench.verify_disposable_container()
    else:
        return bench.verify_disposable_container()
    assert calls == [["docker","inspect",bench.NAME]]


def test_accepts_bounded_disconnected_synthetic_container(monkeypatch):
    verify(monkeypatch,isolated())


@pytest.mark.parametrize("mutation",[
    {"Name":"/neo4j"},
    {"HostConfig":{"NetworkMode":"bridge"}},
    {"HostConfig":{"PortBindings":{"7687":[{"HostPort":"7687"}]}}},
    {"HostConfig":{"NanoCpus":3000000000}},
    {"HostConfig":{"Memory":4*1024*1024*1024}},
    {"Mounts":[{"Type":"bind","Source":"/nas","Destination":"/data"}]},
    {"Config":{"Image":"neo4j:5.26-enterprise"}},
    {"Config":{"Env":["NEO4J_AUTH=neo4j/admin"]}},
    {"State":{"Running":False}},
])
def test_rejects_unsafe_or_unexpected_target(monkeypatch,mutation):
    source=isolated()
    for k,v in mutation.items():
        if k in ("HostConfig","Config","State"):
            source[k].update(v)
        else:
            source[k]=v
    with pytest.raises(RuntimeError,match="UNSAFE_BENCHMARK_CONTAINER|STAGING_CONTAINER_NOT_RUNNING"):
        verify(monkeypatch,source)


def test_missing_container_cannot_seed_anything(monkeypatch):
    verify(monkeypatch,None,rc=1)
