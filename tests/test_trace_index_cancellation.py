"""Negative controls for trace-index physical read capacity and staging safety.

These tests deliberately do NOT assert that soft TTL leases physically fence a
database query. They demonstrate a counterexample using Redis-clock simulation.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).parent))
from test_trace_index_inflight import FakeRedis
from assistx import rate_limiter as limiter
import probe_trace_index_cancellation_526 as probe

def test_expired_soft_lease_can_double_physically_active_queries(monkeypatch):
    fake=FakeRedis()
    monkeypatch.setattr(limiter,"_get_redis",lambda:fake)
    lease=limiter.TraceIndexLeases(max_inflight=1,ttl_seconds=30)
    old,_=lease.acquire()
    assert old and lease.acquire()[0] is None
    # The database query is deliberately *not* finished. Time advances in
    # Redis: the slot expires, but a running query does not magically stop.
    physically_running={old}
    fake.now+=30_001
    successor,_=lease.acquire()
    assert successor and successor!=old
    physically_running.add(successor)
    assert len(physically_running)==2  # exceeds Redis cap=1
    assert len(fake.entries[lease.key])==1
    assert lease.release(old) is False  # cannot free successor
    assert successor in fake.entries[lease.key]
    physically_running.remove(old)
    assert lease.release(successor)
    physically_running.remove(successor)

def stage_info():
    return {
        "Name":"/"+probe.NAME,"State":{"Running":True},
        "Config":{"Image":"neo4j:5.26-enterprise","Env":["NEO4J_AUTH=none"]},
        "HostConfig":{"NetworkMode":probe.NET,"PortBindings":{},
                      "NanoCpus":1_000_000_000,"Memory":2_200*1024*1024},
        "NetworkSettings":{"Networks":{probe.NET:{"IPAddress":"172.23.0.2"}}},
        "Mounts":[{"Type":"volume"}],
    }

def stage_network():
    return [{"Internal":True}]

def check(monkeypatch,obj=None,net=None,fail=False):
    def fake_run(args,**kwargs):
        assert args[0]=="docker"
        assert args[1] in ("inspect","network")
        if fail:return SimpleNamespace(returncode=1,stdout="")
        if args[1]=="inspect":
            assert args==["docker","inspect",probe.NAME]
            return SimpleNamespace(returncode=0,stdout=json.dumps([obj]))
        assert args==["docker","network","inspect",probe.NET]
        return SimpleNamespace(returncode=0,stdout=json.dumps(net))
    monkeypatch.setattr(probe.subprocess,"run",fake_run)
    return probe.checked_uri()

def test_disconnected_disposable_526_is_admitted_by_guard(monkeypatch):
    assert check(monkeypatch,stage_info(),stage_network())=="bolt://172.23.0.2:7687"

@pytest.mark.parametrize("field,value",[
    ("Name","/neo4j"),
    ("State",{"Running":False}),
    ("Config",{"Image":"neo4j:5.23.0","Env":["NEO4J_AUTH=none"]}),
    ("HostConfig",{"NetworkMode":"bridge","PortBindings":{},"NanoCpus":1_000_000_000,"Memory":2_200*1024*1024}),
    ("Mounts",[{"Type":"bind","Source":"/nas","Destination":"/data"}]),
])
def test_guard_rejects_unsafe_target(monkeypatch,field,value):
    obj=stage_info()
    obj[field]=value
    with pytest.raises(RuntimeError,match="UNSAFE_STAGING_TARGET"):
        check(monkeypatch,obj,stage_network())

@pytest.mark.parametrize("port,image,env",[
    ({"7687/tcp":[{"HostIp":"0.0.0.0","HostPort":"17687"}]},"neo4j:5.26-enterprise",["NEO4J_AUTH=none"]),
    ({}, "neo4j:5.26.26-enterprise",["NEO4J_AUTH=none"]),
    ({}, "neo4j:5.26-enterprise",["NEO4J_AUTH=neo4j/secret"]),
])
def test_guard_rejects_port_image_or_auth(monkeypatch,port,image,env):
    obj=stage_info()
    obj["HostConfig"]["PortBindings"]=port
    obj["Config"]={"Image":image,"Env":env}
    with pytest.raises(RuntimeError,match="UNSAFE_STAGING_TARGET"):
        check(monkeypatch,obj,stage_network())

def test_guard_rejects_non_internal_network(monkeypatch):
    with pytest.raises(RuntimeError,match="UNSAFE_STAGING_TARGET"):
        check(monkeypatch,stage_info(),[{"Internal":False}])

def test_guard_rejects_unexpected_target_ip(monkeypatch):
    obj=stage_info()
    obj["NetworkSettings"]["Networks"][probe.NET]["IPAddress"]="10.0.3.2"
    with pytest.raises(RuntimeError,match="UNEXPECTED_STAGING_IP"):
        check(monkeypatch,obj,stage_network())

def test_guard_rejects_missing_container(monkeypatch):
    with pytest.raises(RuntimeError,match="CANNOT_INSPECT_DISPOSABLE"):
        check(monkeypatch,fail=True)
