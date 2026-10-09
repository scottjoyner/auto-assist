"""Offline, synthetic-only tests of heavy trace-index admission."""
import ast
import asyncio
import pathlib
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest

try:
    import redis
except ImportError:
    redis=types.ModuleType("redis")
    class RedisError(Exception):pass
    redis.RedisError=RedisError
    sys.modules["redis"]=redis

from assistx import rate_limiter as subject

API=(pathlib.Path(__file__).resolve().parents[1]/"src/assistx/api.py").read_text()

class FakeRedis:
    def __init__(self):
        self.now=1_000_000
        self.windows={}
        self.calls=[]
        self.lock=threading.Lock()

    def eval(self,script,count,peer,global_key,per_limit,fleet_limit,window,member):
        assert count==2 and script==subject.TRACE_INDEX_LUA
        assert "TIME" in script and "ZADD" in script
        assert "{assistx_trace_index}" in peer and "{assistx_trace_index}" in global_key
        with self.lock:
            self.calls.append((peer,global_key))
            for key in (peer,global_key):
                self.windows[key]=[x for x in self.windows.get(key,[])
                                   if x[0]>self.now-int(window)]
            local=self.windows.get(peer,[])
            all_=self.windows.get(global_key,[])
            if len(local)>=per_limit or len(all_)>=fleet_limit:
                seen=local if len(local)>=per_limit else all_
                retry=max(1,(window-(self.now-seen[0][0])+999)//1000)
                return [0,0,retry]
            before=len(local)
            self.windows.setdefault(peer,[]).append((self.now,member))
            self.windows.setdefault(global_key,[]).append((self.now,member))
            return [1,per_limit-before-1,0]

def fake(monkeypatch):
    r=FakeRedis()
    monkeypatch.setattr(subject,"_get_redis",lambda:r)
    return r

def test_per_peer_global_and_denial_does_not_consume_other_slots(monkeypatch):
    r=fake(monkeypatch);guard=subject.TraceIndexLimiter(per_peer=2,global_max=4,window_seconds=60)
    assert guard.check("p1")== (True,1,0)
    assert guard.check("p1")== (True,0,0)
    assert guard.check("p1")[0] is False
    assert len(r.windows["ratelimit:{assistx_trace_index}:global"])==2
    assert guard.check("p2")[0] is True
    assert guard.check("p2")[0] is True
    assert guard.check("p3")[0] is False
    assert len(r.windows["ratelimit:{assistx_trace_index}:global"])==4

def test_window_rollover(monkeypatch):
    r=fake(monkeypatch);guard=subject.TraceIndexLimiter(per_peer=1,global_max=1,window_seconds=2)
    assert guard.check("p")[0]
    assert not guard.check("p")[0]
    r.now+=2001
    assert guard.check("p")[0]

def test_redis_keys_do_not_contain_raw_ip(monkeypatch):
    r=fake(monkeypatch);subject.TRACE_INDEX_LIMITER.check("198.51.100.19")
    key=r.calls[0][0]
    assert len(key.rsplit(":",1)[-1])==64 and "198.51.100.19" not in key

@pytest.mark.parametrize("peer",[None,"",123,"x"*257])
def test_invalid_peer_fail_closed(monkeypatch,peer):
    r=fake(monkeypatch)
    assert subject.TRACE_INDEX_LIMITER.check(peer)[0] is False
    assert not r.calls

def test_redis_outage_fail_closed(monkeypatch):
    class Offline:
        def eval(self,*args):raise subject.redis_module.RedisError("test-outage")
    monkeypatch.setattr(subject,"_get_redis",lambda:Offline())
    assert subject.TRACE_INDEX_LIMITER.check("p")== (False,0,60)

@pytest.mark.parametrize("value",[None,[],[1],[1,2,3,4],[3,0,0],[-1,0,0],[1,-1,0]])
def test_bad_redis_response_fail_closed(monkeypatch,value):
    class Broken:
        def eval(self,*args):return value
    monkeypatch.setattr(subject,"_get_redis",lambda:Broken())
    assert subject.TRACE_INDEX_LIMITER.check("p")== (False,0,60)

def test_reject_invalid_configuration():
    for args in [(0,60,60),(20,10,60),(1,1001,60),(1,2,3601)]:
        with pytest.raises(ValueError):subject.TraceIndexLimiter(*args)

def request(peer="192.0.2.2",xff="203.0.113.8"):
    return SimpleNamespace(client=SimpleNamespace(host=peer),
                           headers={"X-Forwarded-For":xff})

def test_guard_ignores_spoofed_forwarded_header_and_allows(monkeypatch):
    from assistx import swarm_routes
    class Spy:
        seen=[]
        def check(self,peer):
            self.seen.append(peer)
            return True,11,0
    s=Spy()
    monkeypatch.setitem(sys.modules,"assistx.rate_limiter",
                        SimpleNamespace(TRACE_INDEX_LIMITER=s))
    assert swarm_routes._admit_trace_index(request()) is None
    assert s.seen==["192.0.2.2"]

def test_guard_denies_with_retry_after_before_graph_access(monkeypatch):
    from assistx import swarm_routes
    from fastapi import HTTPException
    class Deny:
        def check(self,peer):return False,0,7
    monkeypatch.setitem(sys.modules,"assistx.rate_limiter",
                        SimpleNamespace(TRACE_INDEX_LIMITER=Deny()))
    with pytest.raises(HTTPException) as e:
        swarm_routes._admit_trace_index(request())
    assert e.value.status_code==429
    assert e.value.headers["Retry-After"]=="7"

def test_authentication_dependency_precedes_admission_in_handler():
    from assistx import swarm_routes
    import inspect
    assert "user" in inspect.signature(swarm_routes.api_list_traces).parameters
    tree=ast.parse((pathlib.Path(__file__).resolve().parents[1]/
        "src/assistx/swarm_routes.py").read_text())
    handler=next(x for x in tree.body if isinstance(x,ast.FunctionDef)
                 and x.name=="api_list_traces")
    # Lease acquired before rate budget; both before opening a graph.
    entry=next(x for x in handler.body if isinstance(x,ast.With))
    assert ast.unparse(entry.items[0].context_expr)=="_hold_trace_index_capacity()"
    guard_index=next(i for i,x in enumerate(entry.body)
                     if isinstance(x,ast.Expr) and isinstance(x.value,ast.Call)
                     and isinstance(x.value.func,ast.Name)
                     and x.value.func.id=="_admit_trace_index")
    neo_index=next(i for i,x in enumerate(entry.body)
                   if isinstance(x,ast.Assign) and isinstance(x.value,ast.Call)
                   and isinstance(x.value.func,ast.Name)
                   and x.value.func.id=="_neo")
    assert guard_index<neo_index
    # FastAPI validates user dependency before entering function body.
    assert "Depends(_default_auth)" in ast.unparse(handler.args)

def test_no_pre_auth_middleware_quota_exhaustion():
    tree=ast.parse(API)
    assignment=next(n for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=="RATE_LIMITED_ROUTES"
                for t in n.targets))
    entries=[(ast.literal_eval(x.elts[0]),ast.literal_eval(x.elts[1]))
             for x in assignment.value.elts]
    assert ("GET","/api/traces") not in entries
    assert ("POST","/api/dispatch") in entries
    assert ("POST","/api/ask") in entries

def test_atomic_simulation_concurrent_burst_never_overadmits(monkeypatch):
    r=fake(monkeypatch);guard=subject.TraceIndexLimiter(per_peer=50,global_max=60,window_seconds=60)
    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted=list(pool.map(lambda i: guard.check("peer-"+str(i))[0],range(180)))
    assert sum(accepted)==60
    assert len(r.windows["ratelimit:{assistx_trace_index}:global"])==60
