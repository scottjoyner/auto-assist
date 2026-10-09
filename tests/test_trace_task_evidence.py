"""Contract tests for on-demand unverified trace-task/registry evidence."""
from dataclasses import dataclass
import pytest

from assistx.swarm_core import get_trace_task_evidence

@dataclass
class FakeResult:
    entries: list
    def single(self):
        return self.entries[0] if self.entries else None
    def __iter__(self):
        return iter(self.entries)

class FakeSession:
    def __init__(self, rows=None, exists=True):
        self.rows = rows or []
        self.exists = exists
        self.requests = []
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def run(self, query, params):
        q = str(query)
        self.requests.append((query,dict(params)))
        assert query.timeout == 4.0
        assert "CREATE " not in q and "MERGE " not in q and "DELETE " not in q
        assert "SET " not in q and "payload_json" not in q and "source" not in q
        assert list(params) == ["correlation_id"]
        if "count(g) AS n" in q:
            return FakeResult([{"n": 1 if self.exists else 0}])
        assert "e.task_id = t.id" in q
        assert "-[:FOR_TASK]->(t:Task)" in q
        assert "LIMIT 13" in q
        assert "OPTIONAL MATCH (n:SwarmNode {node_id:t.node_id})" in q
        assert "count(DISTINCT n)" in q
        assert "RETURN t.id AS task_id" in q
        return FakeResult(self.rows)

class FakeNeo:
    def __init__(self,rows=None, exists=True):
        self.session = FakeSession(rows,exists)
        self.closed=False
    def _session(self):return self.session
    def close(self):self.closed=True

def row(task="t-1",node="node-1",worker="worker-1",matches=1,status="CLAIMED"):
    return {"task_id":task,"node_id":node,"worker_id":worker,
            "registry_matches":matches,"task_status":status}

def test_unverified_registry_match_never_attests_execution():
    neo=FakeNeo([row()])
    r=get_trace_task_evidence(neo,"corr-a")
    assert r["schema"]=="trace-task-evidence-v1"
    assert r["node_or_agent_verified"] is False
    assert r["source_authenticated"] is False
    assert r["graph_write_permitted"] is False
    t=r["tasks"][0]
    assert t["task_id"]=="t-1"
    assert t["registry_state"]=="registry_id_match_unverified"
    assert t["node_or_agent_verified"] is False
    assert t["task_provenance"]=="trace_event_relationship_and_property"
    assert t["projection_provenance"]=="assignment_projection_unverified"
    assert len(neo.session.requests)==2

@pytest.mark.parametrize("node,matches,expected",[
    (None,0,"not_recorded"),("node",0,"not_registered"),
    ("node",1,"registry_id_match_unverified"),
    ("node",2,"ambiguous_registry_id"),("node",20,"ambiguous_registry_id")
])
def test_registry_states_unambiguously_unverified(node,matches,expected):
    r=get_trace_task_evidence(FakeNeo([row(node=node,matches=matches)]),"abc")
    t=r["tasks"][0]
    assert t["registry_state"]==expected
    assert t["registry_matches"]==min(matches,2)
    assert t["node_or_agent_verified"] is False

def test_unknown_trace_returns_none_without_second_query():
    n=FakeNeo(exists=False)
    assert get_trace_task_evidence(n,"notfound") is None
    assert len(n.session.requests)==1

def test_existing_trace_without_links_is_unknown_not_fabricated():
    r=get_trace_task_evidence(FakeNeo([]),"found")
    assert r["tasks"]==[]
    assert r["truncated"] is False
    assert r["node_or_agent_verified"] is False

def test_multiple_conflicting_tasks_remain_separate():
    r=get_trace_task_evidence(FakeNeo([row("a",node="x"),row("b",node="y",matches=0)]),"found")
    assert [e["task_id"] for e in r["tasks"]]==["a","b"]
    assert [e["registry_state"] for e in r["tasks"]]==[
        "registry_id_match_unverified","not_registered"]

def test_13th_row_sets_truncated_and_only_12_returned():
    r=get_trace_task_evidence(FakeNeo([row(task=f"t{i}") for i in range(13)]),"abc")
    assert len(r["tasks"])==12 and r["truncated"] is True

def test_metadata_strings_are_length_and_control_bounded():
    r=get_trace_task_evidence(FakeNeo([
        row(task="good",node="x"*180,worker="w\nbad",status="s"*150),
        row(task="unsafe\nbad"),row(task=None)
    ]),"abc")
    assert len(r["tasks"])==1
    t=r["tasks"][0]
    assert t["node_id"] is None
    assert t["worker_id"] is None
    assert t["task_status"] is None
    assert t["registry_state"]=="not_recorded"

@pytest.mark.parametrize("correlation_id",["","x"*129,None,0])
def test_invalid_correlation_id_rejected_before_read(correlation_id):
    n=FakeNeo()
    with pytest.raises(ValueError):
        get_trace_task_evidence(n,correlation_id)
    assert len(n.session.requests)==0

def test_route_auth_and_path_validation(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from assistx import swarm_routes
    app=FastAPI()
    app.include_router(swarm_routes.router)
    created=[]
    def make_neo():
        neo=FakeNeo([row()])
        created.append(neo)
        return neo
    monkeypatch.setattr(swarm_routes,"_neo",make_neo)
    def require_auth(request,credentials):
        if credentials is None:raise HTTPException(status_code=401,detail="auth required")
        return credentials.username
    monkeypatch.setattr(swarm_routes,"_injected_auth_dependency",require_auth)
    from assistx.trace_preview_access import basic_preview_permitted
    monkeypatch.setattr(swarm_routes,"_trace_metadata_authorizer",
        lambda principal, credentials: basic_preview_permitted(
            principal, credentials,
            configured_user="test",
            configured_password="test",
            allowed_users="test"))
    client=TestClient(app)
    assert client.get("/api/traces/one/evidence").status_code==401
    assert created==[]
    from fastapi.security import HTTPBasicCredentials
    headers={"Authorization":"Basic dGVzdDp0ZXN0"}
    ok=client.get("/api/traces/one/evidence",headers=headers)
    assert ok.status_code==200,ok.text[:160]
    assert ok.json()["tasks"][0]["registry_state"]=="registry_id_match_unverified"
    assert created[0].closed is True
    invalid=client.get("/api/traces/"+("a"*129)+"/evidence",headers=headers)
    assert invalid.status_code==422
    assert len(created)==1
    assert client.get("/api/traces/one/evidence",headers={}).status_code==401
