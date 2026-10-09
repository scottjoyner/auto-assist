"""Synthetic Redis lease and route admission tests; no real fleet traffic."""
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from assistx import rate_limiter as limiter
from assistx import swarm_routes
from fastapi import HTTPException


class FakeRedis:
    """Atomic Redis TIME/ZSET behavior for the one-key lease contract."""
    def __init__(self):
        self.lock = threading.RLock()
        self.now = 1_000_000  # Redis server clock in milliseconds
        self.entries = {}
        self.calls = []
        self.fail = False
        self.malformed = None
        self.malformed_set = False

    def eval(self, script, count, key, *args):
        if self.fail:
            raise limiter.redis_module.RedisError("synthetic unavailable")
        if self.malformed_set:
            return self.malformed
        assert count == 1 and key == limiter.TraceIndexLeases.key
        assert "{assistx_trace_index}" in key
        with self.lock:
            self.calls.append("acquire" if script == limiter.TRACE_INDEX_LEASE_ACQUIRE_LUA else "release")
            leases = self.entries.setdefault(key, {})
            if script == limiter.TRACE_INDEX_LEASE_ACQUIRE_LUA:
                cap, ttl, token = args
                assert "TIME" in script and "ZREMRANGEBYSCORE" in script and "ZADD" in script
                for old in list(leases):
                    if leases[old] <= self.now: del leases[old]
                if len(leases) >= cap:
                    retry=max(1,(min(leases.values())-self.now+999)//1000)
                    return [0,len(leases),retry]
                if token in leases:
                    return [0,len(leases),1]
                leases[token]=self.now+ttl
                return [1,len(leases),0]
            if script == limiter.TRACE_INDEX_LEASE_RELEASE_LUA:
                assert "ZREM" in script
                token=args[0]
                return int(leases.pop(token,None) is not None)
            raise AssertionError("Unexpected Redis script")


def setup(monkeypatch,cap=3):
    fake=FakeRedis()
    monkeypatch.setattr(limiter,"_get_redis",lambda:fake)
    lease=limiter.TraceIndexLeases(max_inflight=cap,ttl_seconds=30)
    monkeypatch.setattr(limiter,"TRACE_INDEX_LEASES",lease)
    return fake,lease


@pytest.mark.parametrize("clients",[1,3,5,10])
def test_burst_never_admits_more_than_configured_global_slots(monkeypatch,clients):
    redis,lease=setup(monkeypatch,cap=3)
    with ThreadPoolExecutor(max_workers=clients) as pool:
        admitted=list(pool.map(lambda _:lease.acquire(),range(clients)))
    tokens=[t for t,_ in admitted if t]
    assert len(tokens)==min(clients,3)
    assert len(set(tokens))==len(tokens)
    assert len(redis.entries[lease.key])<=3
    for t in tokens:
        assert lease.release(t) is True
    assert redis.entries[lease.key]=={}


def test_exact_token_fences_stale_release_and_replay(monkeypatch):
    r,lease=setup(monkeypatch,cap=1)
    first,_=lease.acquire()
    assert len(first)==32
    assert lease.release(first) is True
    replacement,_=lease.acquire()
    assert replacement!=first
    assert lease.release(first) is False
    assert lease.acquire()[0] is None
    assert lease.release(replacement) is True
    assert lease.acquire()[0] is not None


def test_crashed_worker_lease_expires_by_redis_server_time(monkeypatch):
    r,lease=setup(monkeypatch,cap=1)
    old,_=lease.acquire()
    assert lease.acquire()[0] is None
    r.now+=30001
    new,_=lease.acquire()
    assert new and new!=old
    assert lease.release(old) is False
    assert lease.acquire()[0] is None  # old token did not free successor
    assert lease.release(new) is True


def test_deny_retry_after_is_clamped_and_remaining_lease_survives(monkeypatch):
    r,lease=setup(monkeypatch,cap=1)
    first,_=lease.acquire()
    r.now+=15000
    got,retry=lease.acquire()
    assert got is None and retry==15
    assert len(r.entries[lease.key])==1
    assert lease.release(first)


@pytest.mark.parametrize("raw",[None,[],[1],[0,0,0,0],["nope",1,0],[-1,0,0],[1,0,0],[1,1,1],[1,5,0]])
def test_malformed_redis_leases_fail_closed(monkeypatch,raw):
    redis,lease=setup(monkeypatch)
    redis.malformed=raw
    redis.malformed_set=True
    assert lease.acquire()==(None,30)


def test_redis_unavailable_fails_closed_and_no_graph(monkeypatch):
    redis,lease=setup(monkeypatch)
    redis.fail=True
    assert lease.acquire()==(None,30)
    called=[]
    monkeypatch.setattr(swarm_routes,"_neo",lambda:called.append("graph"))
    with pytest.raises(HTTPException) as err:
        swarm_routes.api_list_traces(
            request=types.SimpleNamespace(client=types.SimpleNamespace(host="198.51.100.1")),
            limit=10,offset=0,search=None,outcome="failed",user="verified",
        )
    assert err.value.status_code==429
    assert err.value.headers["Retry-After"]=="30"
    assert called==[]


def test_no_lease_is_held_after_success_graph_failure_or_rate_denial(monkeypatch):
    redis,lease=setup(monkeypatch,cap=1)
    r=types.SimpleNamespace(client=types.SimpleNamespace(host="192.0.2.1"))
    calls=[]
    class Graph:
        def close(self):calls.append("closed")
    monkeypatch.setattr(swarm_routes,"_neo",lambda:Graph())
    monkeypatch.setattr(swarm_routes,"_admit_trace_index",lambda req:calls.append("rate"))
    monkeypatch.setattr(swarm_routes,"list_traces",lambda *a,**k:{"total":1,"traces":[]})
    result=swarm_routes.api_list_traces(request=r,limit=10,offset=0,search=None,outcome=None,user="verified")
    assert result["total"]==1
    assert calls==["rate","closed"]
    assert not redis.entries[lease.key]

    def no_graph(*a,**k):raise RuntimeError("synthetic graph failure")
    monkeypatch.setattr(swarm_routes,"list_traces",no_graph)
    with pytest.raises(RuntimeError,match="synthetic graph"):
        swarm_routes.api_list_traces(request=r,limit=10,offset=0,search=None,outcome="failed",user="verified")
    assert not redis.entries[lease.key]

    def no_rate(req):raise HTTPException(status_code=429,detail="synthetic budget denied")
    monkeypatch.setattr(swarm_routes,"_admit_trace_index",no_rate)
    prev=len(calls)
    with pytest.raises(HTTPException) as err:
        swarm_routes.api_list_traces(request=r,limit=10,offset=0,search=None,outcome="failed",user="verified")
    assert err.value.status_code==429
    assert len(calls)==prev  # Neo4j never opened after rate guard denial
    assert not redis.entries[lease.key]


def test_concurrency_denied_does_not_burn_minute_quota(monkeypatch):
    redis,lease=setup(monkeypatch,cap=1)
    existing,_=lease.acquire()
    rate=[]
    monkeypatch.setattr(swarm_routes,"_admit_trace_index",lambda request:rate.append(1))
    monkeypatch.setattr(swarm_routes,"_neo",lambda:pytest.fail("Graph opened despite occupied lease"))
    with pytest.raises(HTTPException) as e:
        swarm_routes.api_list_traces(
            request=types.SimpleNamespace(client=types.SimpleNamespace(host="192.0.2.1")),
            limit=50,offset=0,search=None,outcome=None,user="verified"
        )
    assert e.value.status_code==429
    assert rate==[]
    assert lease.release(existing)


def test_lease_release_errors_fail_closed_without_unhandled_exception(monkeypatch):
    redis,lease=setup(monkeypatch,cap=1)
    tok,_=lease.acquire()
    redis.fail=True
    assert lease.release(tok) is False
    assert lease.acquire()==(None,30)


@pytest.mark.parametrize("bad",["",None,123,"e"*31,"x"*32])
def test_invalid_release_token_cannot_mutate_redis(monkeypatch,bad):
    r,lease=setup(monkeypatch)
    assert lease.release(bad) is False
    assert r.calls==[]


def test_invalid_configuration():
    for cfg in [(0,30),(17,30),(1,9),(1,121)]:
        with pytest.raises(ValueError):
            limiter.TraceIndexLeases(*cfg)


def test_fastapi_auth_precedes_lease_and_input_validation(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app=FastAPI()
    app.include_router(swarm_routes.router)
    r,lease=setup(monkeypatch)
    def require_auth(request, credentials):
        if credentials is None:raise HTTPException(status_code=401,detail="Auth")
        return credentials.username
    monkeypatch.setattr(swarm_routes,"_injected_auth_dependency",require_auth)
    client=TestClient(app)
    assert client.get("/api/traces?outcome=failed").status_code==401
    assert len(r.calls)==0
    app.dependency_overrides[swarm_routes._default_auth]=lambda:"verified"
    assert client.get("/api/traces?limit=500").status_code==422
    assert client.get("/api/traces?outcome=invalid").status_code==422
    assert r.calls==[]
