"""Metadata-only trace pagination / opt-in payload previews: synthetic contract tests."""
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx.trace_detail_pages import (
    InvalidTraceCursor, decode_cursor, encode_cursor,
    get_trace_page, get_trace_payload_preview
)
from assistx import swarm_routes


def event(i, ts=100):
    return {
        "event_id": f"evt-{i:04d}", "ts_ms": ts,
        "event_type": "task.started", "source": "synthetic producer",
        "task_id": f"task-{i}", "dispatch_id": "",
        "route_id": "", "assignment_id": "",
    }


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __enter__(self):return self
    def __exit__(self,*args):return False

    def run(self, statement, params):
        query = str(statement)
        self.calls.append((query,dict(params),statement.timeout))
        assert statement.timeout == 4.0
        for token in ("CREATE ", "MERGE ", "DELETE ", " SET ", "DETACH"):
            assert token not in query
        if "AS payload_chars" in query:
            assert "t.event_id AS event_id" not in query
            cid = params["correlation_id"]
            found = [
                x for x in self.rows
                if x.get("correlation_id","cid-one")==cid
                and x["event_id"]==params["event_id"]
            ]
            return [
                {"preview":x.get("payload_json","")[:params["preview_chars"]],
                 "payload_chars":len(x.get("payload_json",""))}
                for x in found
            ]
        assert "payload_json" not in query
        assert "RETURN t\n" not in query  # forbid whole-node projections, not t.event_id
        assert "SKIP " not in query
        assert "ORDER BY ts_ms DESC, event_id DESC" in query
        assert params["take"] <= 101
        found = [
            x for x in self.rows
            if x.get("correlation_id","cid-one")==params["correlation_id"]
            and (params["before_ts"] is None
                 or x["ts_ms"] < params["before_ts"]
                 or (x["ts_ms"]==params["before_ts"] and x["event_id"]<params["before_id"]))
        ]
        found.sort(key=lambda e:(e["ts_ms"],e["event_id"]),reverse=True)
        return [
            {k:x.get(k) for k in ("event_id","ts_ms","event_type","source",
                                  "task_id","dispatch_id","route_id","assignment_id")}
            for x in found[:params["take"]]
        ]


class FakeNeo:
    def __init__(self,rows):
        self.session = FakeSession(rows)
        self.closed=False

    def _session(self):return self.session
    def close(self):self.closed=True


@pytest.mark.parametrize("n", [1,2,80,1001])
def test_equal_timestamp_paged_without_skips_or_duplicates(n):
    neo=FakeNeo([event(i,ts=500) for i in range(n)])
    cursor=None
    seen=[]
    for _ in range(25):
        page=get_trace_page(neo,"cid-one",limit=80,cursor=cursor)
        assert page["metadata_only"] is True
        assert page["historical_retention_proven"] is False
        assert page["total_indexed_events"] is None
        assert len(page["events"])<=80
        assert all("payload_json" not in e for e in page["events"])
        seen += [e["event_id"] for e in page["events"]]
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor=page["next_cursor"]
        assert decode_cursor(cursor) == {"ts_ms":500,"event_id":seen[-1]}
    assert seen == [f"evt-{i:04d}" for i in reversed(range(n))]
    assert len(seen)==len(set(seen))


def test_mixed_times_tie_break_keyset():
    rows=[event(1,900),event(2,900),event(3,800),event(4,800),event(5,700)]
    neo=FakeNeo(rows)
    a=get_trace_page(neo,"cid-one",limit=2)
    b=get_trace_page(neo,"cid-one",limit=2,cursor=a["next_cursor"])
    c=get_trace_page(neo,"cid-one",limit=2,cursor=b["next_cursor"])
    assert [e["event_id"] for e in a["events"]+b["events"]+c["events"]] == [
        "evt-0002","evt-0001","evt-0004","evt-0003","evt-0005"
    ]


@pytest.mark.parametrize("invalid",["", "%", "abc===", "é"*1000, "A"*721, "../path",
    base64.urlsafe_b64encode(b'{"v":1,"t":true,"id":"evt-001"}').decode().rstrip("="),
    base64.urlsafe_b64encode(b'{"v":1,"t":1,"id":"ok","extra":2}').decode().rstrip("="),
    base64.urlsafe_b64encode(b'{"v":1,"t":2,"t":3,"id":"ok"}').decode().rstrip("="),
    base64.urlsafe_b64encode(b'{"v":2,"t":1,"id":"ok"}').decode().rstrip("=")])
def test_bad_cursors_fail_before_graph(invalid):
    neo=FakeNeo([event(1)])
    with pytest.raises(InvalidTraceCursor):
        get_trace_page(neo,"cid-one",cursor=invalid)
    assert neo.session.calls == []


@pytest.mark.parametrize("size",[-1,0,True,101,"80"])
def test_page_size_rejected(size):
    neo=FakeNeo([event(1)])
    with pytest.raises(ValueError):
        get_trace_page(neo,"cid-one",limit=size)
    assert not neo.session.calls


def test_empty_and_nonmatching_correlation():
    neo=FakeNeo([])
    page=get_trace_page(neo,"cid-one")
    assert page["events"]==[]
    assert page["has_more"] is False
    assert page["total_indexed_events"] is None


def test_event_context_fields_are_scalar_bounded_and_never_payload():
    x=event(1)
    x["source"]="<synthetic>" + "x"*300
    x["task_id"]="unsafe\nlog"
    x["payload_json"]="SENSITIVE_SYNTHETIC_TEST_PAYLOAD"
    page=get_trace_page(FakeNeo([x]),"cid-one")
    assert len(page["events"][0]["source"]) == 160
    assert page["events"][0]["task_id"] is None
    assert "SENSITIVE_SYNTHETIC_TEST_PAYLOAD" not in json.dumps(page)


def test_payload_opt_in_requires_same_correlation():
    one=event(1);one.update({"payload_json":"SYNTHETIC_TEST_ONLY" * 600,"correlation_id":"cid-one"})
    two=event(2);two.update({"payload_json":"PRIVATE_OTHER_FIXTURE","correlation_id":"cid-two"})
    neo=FakeNeo([one,two])
    assert get_trace_payload_preview(neo,"cid-one",two["event_id"]) is None
    result=get_trace_payload_preview(neo,"cid-one",one["event_id"])
    assert result["truncated"] is True
    assert len(result["payload_preview"])==4096
    assert result["historical_retention_proven"] is False
    assert "PRIVATE_OTHER_FIXTURE" not in json.dumps(result)
    assert neo.session.calls[-1][1]["preview_chars"]==4096


def test_payload_fails_on_duplicate_event_ownership():
    x=event(1)
    neo=FakeNeo([x,x])
    with pytest.raises(RuntimeError,match="Ambiguous"):
        get_trace_payload_preview(neo,"cid-one",x["event_id"])


def app_with_fixture(monkeypatch, enable):
    app=FastAPI()
    app.include_router(swarm_routes.router)
    stored=[]
    def factory():
        n=FakeNeo([event(1),event(2)])
        stored.append(n)
        return n
    monkeypatch.setattr(swarm_routes,"_neo",factory)
    monkeypatch.setenv("ASSISTX_TRACE_DETAIL_PAGING_ENABLED","1" if enable else "0")
    def injected(request, credentials):
        if credentials is None or credentials.username!="fixture":
            raise HTTPException(status_code=401,detail="Authentication required")
        return "fixture-operator"
    monkeypatch.setattr(swarm_routes,"_injected_auth_dependency",injected)
    return TestClient(app),stored


def test_feature_disabled_and_anonymous_requests_never_open_graph(monkeypatch):
    client,store=app_with_fixture(monkeypatch,False)
    for method,url in [
        ("GET","/api/traces/cid-one/timeline"),
        ("POST","/api/traces/cid-one/payload-preview")]:
        resp=client.request(method,url,auth=("fixture","pw"),json={"event_id":"evt-0001"} if method=="POST" else None)
        assert resp.status_code == 503
    assert store==[]
    response=client.get("/api/traces/cid-one/timeline")
    assert response.status_code==401
    assert store==[]


def test_feature_enabled_auth_and_validation_before_reads(monkeypatch):
    client,store=app_with_fixture(monkeypatch,True)
    assert client.get("/api/traces/cid-one/timeline").status_code==401
    assert client.get("/api/traces/cid-one/timeline?limit=101",auth=("fixture","pw")).status_code==422
    assert client.get("/api/traces/cid-one/timeline?cursor=%25",auth=("fixture","pw")).status_code==422
    assert client.post("/api/traces/cid-one/payload-preview",auth=("fixture","pw"),json={"event_id":"x","extra":1}).status_code==422
    assert store==[] or all(x.closed for x in store)
    valid=client.get("/api/traces/cid-one/timeline?limit=1",auth=("fixture","pw"))
    assert valid.status_code==200,valid.text[:400]
    assert valid.json()["returned"]==1
    assert valid.json()["next_cursor"]
    assert store[-1].closed


def test_feature_requires_injected_auth_even_with_basic_header(monkeypatch):
    client,store=app_with_fixture(monkeypatch,True)
    monkeypatch.setattr(swarm_routes,"_injected_auth_dependency",None)
    reply=client.get("/api/traces/cid-one/timeline",auth=("anyone","anything"))
    assert reply.status_code==503
    assert store==[]

def test_payload_preview_requires_explicit_authenticated_post_and_no_store(monkeypatch):
    client, store = app_with_fixture(monkeypatch, True)
    assert client.get("/api/traces/cid-one/payload-preview", auth=("fixture","pw")).status_code == 405
    result = client.post(
        "/api/traces/cid-one/payload-preview",
        auth=("fixture","pw"), json={"event_id":"evt-0001"}
    )
    assert result.status_code == 200, result.text[:250]
    assert result.headers["cache-control"] == "no-store, private"
    assert result.json()["schema"] == "trace-payload-preview-v1"
    assert result.json()["event_id"] == "evt-0001"
    assert result.json()["payload_preview"] == ""
    assert store[-1].closed

def test_metadata_page_has_no_store_and_never_projects_private_payload(monkeypatch):
    client, store = app_with_fixture(monkeypatch, True)
    result = client.get("/api/traces/cid-one/timeline?limit=2",auth=("fixture","pw"))
    assert result.status_code==200
    assert result.headers["cache-control"] == "no-store, private"
    assert result.json()["metadata_only"] is True
    assert all("payload_json" not in row for row in result.json()["events"])
    assert store[-1].closed
