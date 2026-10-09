"""Explicitly invoked synthetic acceptance of the *full* AssistX FastAPI app.

No sockets, production credentials, graph writes, or real provider calls.
Caller must launch within an isolated container with explicitly injected
synthetic Basic Auth credentials and no external network access.
"""
from __future__ import annotations

import os
import sys
from fastapi.testclient import TestClient
from fastapi import HTTPException


def main() -> int:
    if os.getenv("ASSISTX_ISOLATED_TRACE_AUTH_CANARY") != "synthetic-explicit-opt-in":
        print("BLOCKED: explicitly isolated synthetic environment required")
        return 2
    if (os.getenv("BASIC_AUTH_USER"), os.getenv("BASIC_AUTH_PASS")) != (
        "synthetic-operator", "synthetic-test-password-only"
    ):
        print("BLOCKED: canary requires fixed synthetic credentials")
        return 2
    if os.getenv("TRUSTED_AUTH_HEADER"):
        print("BLOCKED: trusted-header auth cannot be tested by this canary")
        return 2
    import logging
    logging.disable(logging.CRITICAL)
    from assistx import api, swarm_routes
    from assistx.trace_read_budget import TraceReadBudgetUnavailable

    assert swarm_routes._injected_auth_dependency is api.auth
    assert api.USER == "synthetic-operator" and api.PASS == "synthetic-test-password-only"
    assert not api.TRUSTED_AUTH_HEADER
    reads = []
    quota = []
    class FakeNeo:
        def __init__(self): self.closed = False
        def close(self): self.closed = True
    def fake_neo():
        n = FakeNeo()
        reads.append(n)
        return n
    def index(neo, **kwargs):
        return {"total": 1, "limit": kwargs.get("limit", 50),
                "offset": kwargs.get("offset", 0), "outcome": kwargs.get("outcome") or "all",
                "traces": [{"correlation_id": "synthetic-id", "outcome": "completed"}]}
    def detail(neo, cid):
        return {"correlation_id": cid, "events": [], "current_state": "synthetic"}
    def evidence(neo, cid):
        return {"schema": "trace-task-evidence-v1", "correlation_id": cid,
                "source_authenticated": False, "node_or_agent_verified": False,
                "graph_write_permitted": False, "tasks": [],
                "trust": "unverified_correspondence_not_execution_attestation"}
    swarm_routes._neo = fake_neo
    swarm_routes.list_traces = index
    swarm_routes.get_trace = detail
    swarm_routes.get_trace_task_evidence = evidence
    client = TestClient(api.app, raise_server_exceptions=False)
    valid = ("synthetic-operator", "synthetic-test-password-only")
    invalid = ("synthetic-operator", "bad-synthetic")
    urls = ["/traces", "/api/traces", "/api/traces/synthetic-id", "/api/traces/synthetic-id/evidence"]
    for path in urls:
        for auth in (None, invalid):
            before = len(reads)
            kwargs = {} if auth is None else {"auth": auth}
            resp = client.get(path, **kwargs)
            assert resp.status_code == 401, (path, "no/wrong credentials", resp.status_code)
            assert len(reads) == before
        fake_forward = client.get(path, headers={"X-Forwarded-For": "127.0.0.1", "X-Synthetic-Operator": "synthetic-operator"})
        assert fake_forward.status_code == 401, (path, "untrusted header", fake_forward.status_code)
    print("PASS anonymous, incorrect Basic and spoofed forwarded header rejected on all 4 routes")
    for path in urls:
        before = len(reads)
        resp = client.get(path, auth=valid)
        assert resp.status_code == 200, (path, resp.status_code, resp.text[:200])
        if path == "/traces":
            assert len(reads) == before
        else:
            assert len(reads) == before + 1 and reads[-1].closed
    print("PASS valid synthetic Basic uses real FastAPI app; 3 read-only graph mocks closed")
    for path in ("/api/traces/" + "a"*129, "/api/traces/" + "a"*129 + "/evidence", "/api/traces?limit=201"):
        before = len(reads)
        resp = client.get(path, auth=valid)
        assert resp.status_code == 422, (path[:25], resp.status_code)
        assert len(reads) == before
    print("PASS malformed bounds rejected before graph access")
    os.environ["ASSISTX_TRACE_READ_BUDGET_MODE"] = "off"
    def should_not_call(principal): raise AssertionError("quota called when disabled")
    swarm_routes.check_trace_read_budget = should_not_call
    assert client.get("/api/traces", auth=valid).status_code == 200
    print("PASS default-off never touches quota backend")
    os.environ["ASSISTX_TRACE_READ_BUDGET_MODE"] = "enforce"
    def denied(principal):
        quota.append(principal)
        return (False, 0, 11)
    swarm_routes.check_trace_read_budget = denied
    for path in urls[1:]:
        before = len(reads)
        resp = client.get(path, auth=valid)
        assert resp.status_code == 429 and resp.headers.get("Retry-After") == "11"
        assert len(reads) == before
    assert quota == ["synthetic-operator"] * 3
    print("PASS enabled synthetic budget blocks all 3 GETs with 429 / Retry-After before graph")
    quota.clear()
    before = len(reads)
    assert client.get("/api/traces", headers={}).status_code == 401
    assert not quota and len(reads) == before
    print("PASS real app authentication precedes budget")
    def unavailable(principal): raise TraceReadBudgetUnavailable("synthetic redis outage")
    swarm_routes.check_trace_read_budget = unavailable
    for path in urls[1:]:
        before = len(reads)
        resp = client.get(path, auth=valid)
        assert resp.status_code == 503 and resp.headers.get("Retry-After") == "5"
        assert len(reads) == before
    print("PASS unavailable quota yields 503 and no graph session")
    os.environ["ASSISTX_TRACE_READ_BUDGET_MODE"] = "unknown-mode"
    resp = client.get("/api/traces", auth=valid)
    assert resp.status_code == 503
    print("PASS mode typo cannot silently disable quota")
    os.environ["ASSISTX_TRACE_READ_BUDGET_MODE"] = "off"
    print("ISOLATED_FASTAPI_TRACE_AUTH_CANARY_PASS")
    return 0


def trusted_header_canary() -> int:
    """Record a conditional trust-header concern only in isolated code.

    This does not prove a live proxy forwards caller-supplied values.
    """
    if (os.getenv("BASIC_AUTH_USER"), os.getenv("BASIC_AUTH_PASS")) != (
        "synthetic-operator", "synthetic-test-password-only"
    ) or os.getenv("TRUSTED_AUTH_HEADER") != "X-Synthetic-Proxy-Identity":
        print("BLOCKED: untrusted synthetic test configuration")
        return 2
    import logging
    logging.disable(logging.CRITICAL)
    from assistx import api, swarm_routes
    reads = []
    class FakeNeo:
        def close(self): pass
    def fake_neo():
        reads.append(1)
        return FakeNeo()
    swarm_routes._neo = fake_neo
    swarm_routes.list_traces = lambda neo, **kwargs: {
        "total": 0, "traces": [], "limit": 50, "outcome": "all"
    }
    client = TestClient(api.app, raise_server_exceptions=False)
    no_auth = client.get("/api/traces")
    injected = client.get("/api/traces", headers={
        "X-Synthetic-Proxy-Identity": "unverified-fixture-claim"
    })
    page = client.get("/traces", headers={
        "X-Synthetic-Proxy-Identity": "unverified-fixture-claim"
    })
    assert no_auth.status_code == 401, no_auth.status_code
    assert injected.status_code == 200, injected.status_code
    assert page.status_code == 200, page.status_code
    assert len(reads) == 1
    print("PASS isolated configured synthetic trusted header yields 200 without Basic")
    print("PASS no Basic and no trusted header yields 401")
    print("CONDITIONAL_TRUST_BOUNDARY_BLOCKED_UNTIL_UPSTREAM_STRIPPING_PROVEN")
    return 0


if __name__ == "__main__":
    if os.getenv("ASSISTX_ISOLATED_TRACE_AUTH_CANARY") == "synthetic-trusted-header-research":
        sys.exit(trusted_header_canary())
    sys.exit(main())
