"""Offline contract tests for global, read-only trace outcome filtering.

No Neo4j server, private trace contents, NAS, routers or model calls.
"""
import re
from dataclasses import dataclass

import pytest

from assistx.swarm_core import list_traces


GROUPS = [
    {"cid": "mixed", "types": ["task.accepted", "task.failed"], "ts": 900},
    {"cid": "fail-only", "types": ["task.failed"], "ts": 800},
    {"cid": "completed", "types": ["task.completed"], "ts": 700},
    {"cid": "accepted", "types": ["task.accepted"], "ts": 600},
    {"cid": "open", "types": ["task.started", "task.progress"], "ts": 500},
    {"cid": "no-events", "types": [], "ts": 400},
]


def outcome_of(types):
    if any(x.endswith(".failed") for x in types):
        return "failed"
    if any(x.endswith((".completed", ".accepted")) for x in types):
        return "completed"
    return "open"


@dataclass
class FakeResult:
    rows: list

    def single(self):
        return self.rows[0]

    def __iter__(self):
        return iter(self.rows)


class FakeSession:
    def __init__(self):
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, cypher, parameters):
        query = str(cypher)
        params = dict(parameters)
        self.requests.append((cypher, query, params))
        assert cypher.timeout == 4.0
        assert "CREATE " not in query
        assert "MERGE " not in query
        assert "DELETE " not in query
        assert "SET " not in query
        assert "payload_json" not in query
        assert "$limit" not in query or params["limit"] <= 200
        # Derive the filter directly from the count/page predicate region,
        # independent from the presentation's derived CASE expression.
        predicates = query.split("MATCH (g:TraceGroup)", 1)[1]
        predicates = predicates.split(" RETURN count(g) AS n", 1)[0]
        predicates = predicates.split(" MATCH (g)-[:HAS_EVENT]->(t:TraceEvent)", 1)[0]
        has_failed = (
            "EXISTS { MATCH (g)-[:HAS_EVENT]->(failed:TraceEvent)"
            " WHERE failed.event_type ENDS WITH '.failed' }"
        )
        has_done = (
            "EXISTS { MATCH (g)-[:HAS_EVENT]->(done:TraceEvent)"
            " WHERE done.event_type ENDS WITH '.completed'"
            " OR done.event_type ENDS WITH '.accepted' }"
        )
        chosen = None
        if "NOT " + has_failed in predicates:
            chosen = "open" if "NOT " + has_done in predicates else "completed"
        elif has_failed in predicates:
            chosen = "failed"
        assert "EXISTS { MATCH (g)-[:HAS_EVENT]->(:TraceEvent) }" in predicates
        assert chosen in (None, "failed", "completed", "open")
        rows = [
            x for x in GROUPS
            if x["types"]
            and (not params.get("search") or params["search"].lower() in x["cid"].lower())
            and (chosen is None or outcome_of(x["types"]) == chosen)
        ]
        if "RETURN count(g) AS n" in query:
            return FakeResult([{"n": len(rows)}])
        assert "ORDER BY last_ts DESC" in query
        assert "SKIP $offset LIMIT $limit" in query
        return FakeResult([{
            "correlation_id": x["cid"],
            "events": len(x["types"]),
            "first_ts": x["ts"] - 10,
            "last_ts": x["ts"],
            "types": x["types"],
            "outcome": outcome_of(x["types"]),
        } for x in rows[params["offset"]:params["offset"] + params["limit"]]])


class FakeNeo:
    def __init__(self):
        self.session = FakeSession()

    def _session(self):
        return self.session


@pytest.mark.parametrize(
    "outcome,cids",
    [
        (None, ["mixed", "fail-only", "completed", "accepted", "open"]),
        ("failed", ["mixed", "fail-only"]),
        ("completed", ["completed", "accepted"]),
        ("open", ["open"]),
    ],
)
def test_outcome_filters_all_groups_before_limit(outcome, cids):
    neo = FakeNeo()
    result = list_traces(neo, limit=50, outcome=outcome)
    assert result["outcome"] == (outcome or "all")
    assert result["total"] == len(cids)
    assert [x["correlation_id"] for x in result["traces"]] == cids
    assert len(neo.session.requests) == 2
    assert neo.session.requests[0][2] == neo.session.requests[1][2]


def test_filtered_total_is_not_current_page_count():
    neo = FakeNeo()
    result = list_traces(neo, limit=1, offset=1, outcome="failed")
    assert result["total"] == 2
    assert result["limit"] == 1
    assert result["offset"] == 1
    assert [x["correlation_id"] for x in result["traces"]] == ["fail-only"]


def test_global_filter_and_case_insensitive_id_search_compose():
    neo = FakeNeo()
    result = list_traces(neo, search="MIX", outcome="failed")
    assert result["total"] == 1
    assert [x["correlation_id"] for x in result["traces"]] == ["mixed"]
    assert all(req[2]["search"] == "MIX" for req in neo.session.requests)


def test_failed_outcome_takes_precedence_over_completed():
    neo = FakeNeo()
    failed = list_traces(neo, outcome="failed")
    done = list_traces(neo, outcome="completed")
    assert "mixed" in [x["correlation_id"] for x in failed["traces"]]
    assert "mixed" not in [x["correlation_id"] for x in done["traces"]]


def test_untrusted_search_is_parameterized_not_placed_into_cypher():
    text = "' ) DETACH DELETE g //"
    neo = FakeNeo()
    list_traces(neo, search=text, outcome="failed")
    for _, query, params in neo.session.requests:
        assert text not in query
        assert params["search"] == text


@pytest.mark.parametrize("invalid", ["FAILED", "all", "", "other", "failed' DETACH DELETE g"])
def test_outcome_fails_closed(invalid):
    neo = FakeNeo()
    with pytest.raises(ValueError):
        list_traces(neo, outcome=invalid)
    assert neo.session.requests == []


def test_bounds_clamp_and_overlong_search_rejected():
    neo = FakeNeo()
    bounded = list_traces(neo, limit=999, offset=-22)
    assert bounded["limit"] == 200 and bounded["offset"] == 0
    assert all(r[2]["limit"] == 200 and r[2]["offset"] == 0 for r in neo.session.requests)
    with pytest.raises(ValueError):
        list_traces(neo, search="x" * 129)


def test_endpoint_literal_validation_contract():
    # Import the existing route without importing api.py (which currently
    # requires optional langgraph in unrelated dashboard test collection).
    from assistx.swarm_routes import api_list_traces
    from typing import get_type_hints, get_args

    hints = get_type_hints(api_list_traces)
    literal = get_args(hints["outcome"])[0]
    assert set(get_args(literal)) == {"failed", "completed", "open"}


def test_fastapi_route_filters_before_paging_and_requires_auth(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from assistx import swarm_routes

    app = FastAPI()
    app.include_router(swarm_routes.router)

    created = []
    class ManagedNeo(FakeNeo):
        def close(self):
            self.closed = True

    def make_neo():
        n = ManagedNeo()
        created.append(n)
        return n

    monkeypatch.setattr(swarm_routes, "_neo", make_neo)
    # api.py injects its authentication dependency in production. Reproduce
    # that integration explicitly; the bare router alone has a test fallback.
    from fastapi import HTTPException
    def require_fixture_auth(request, credentials):
        if credentials is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return credentials.username
    monkeypatch.setattr(swarm_routes, "_injected_auth_dependency", require_fixture_auth)
    client = TestClient(app)
    unauth = client.get("/api/traces?outcome=failed")
    assert unauth.status_code in (401, 403)
    assert created == []

    # Functional test bypasses auth only through FastAPI's explicit
    # dependency override, not by changing app/auth production code.
    app.dependency_overrides[swarm_routes._trace_read_auth] = lambda: "fixture-user"
    ok = client.get("/api/traces?outcome=failed&limit=1&offset=1")
    assert ok.status_code == 200, ok.text[:300]
    body = ok.json()
    assert body["total"] == 2
    assert body["outcome"] == "failed"
    assert body["limit"] == 1
    assert [row["correlation_id"] for row in body["traces"]] == ["fail-only"]
    assert len(created) == 1 and created[0].closed is True

    # Invalid category, excessive page size and overlong ID searches fail
    # during route input validation without opening a graph session.
    for query in ("outcome=failed%20DELETE", "limit=10000", "offset=-1",
                  "search=" + "a" * 129):
        invalid = client.get("/api/traces?" + query)
        assert invalid.status_code == 422, (query[:30], invalid.status_code)
    assert len(created) == 1


def test_read_only_aggregate_has_no_event_payload_projection():
    neo = FakeNeo()
    list_traces(neo, outcome="failed")
    count, page = [req[1] for req in neo.session.requests]
    assert "payload_json" not in count
    assert "payload_json" not in page
    assert "RETURN count(g) AS n" in count
    assert "RETURN g.correlation_id" in page
    assert "t.event_type" in page
    assert "t.payload" not in page
