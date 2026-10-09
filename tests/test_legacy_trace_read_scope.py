"""Legacy trace GET routes: deny forged headers and payload reads before graph."""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx import swarm_routes
from assistx.trace_preview_access import basic_preview_permitted


INDEX = "/api/traces"
DETAIL = "/api/traces/synthetic-correl"
EVIDENCE = DETAIL + "/evidence"
READS = (INDEX, DETAIL, EVIDENCE)


def _client(monkeypatch):
    app = FastAPI()
    app.include_router(swarm_routes.router)
    graph_calls = []

    class DummyNeo:
        def close(self):
            pass

    def neo():
        graph_calls.append(True)
        return DummyNeo()

    def header_first_auth(request, credentials):
        # Models the real api.auth behavior before trusted-header ingress proof:
        # client-injected identity takes precedence even over supplied Basic.
        user = request.headers.get("X-Synthetic-Proxy-Identity")
        if user:
            return user
        if credentials is not None:
            return credentials.username
        raise HTTPException(status_code=401, detail="Unauthenticated")

    scope = lambda user, credentials, allowlist: basic_preview_permitted(
        user, credentials,
        configured_user="synthetic-operator",
        configured_password="fixture-pw",
        allowed_users=allowlist,
    )
    monkeypatch.setattr(swarm_routes, "_injected_auth_dependency", header_first_auth)
    monkeypatch.setattr(swarm_routes, "_trace_metadata_authorizer",
                        lambda user, cred: scope(user, cred, "synthetic-operator"))
    monkeypatch.setattr(swarm_routes, "_trace_preview_authorizer",
                        lambda user, cred: scope(user, cred, ""))  # default deny
    monkeypatch.setattr(swarm_routes, "_neo", neo)
    monkeypatch.delenv("ASSISTX_LEGACY_TRACE_DETAIL_ENABLED", raising=False)
    return TestClient(app), graph_calls


def test_forged_identity_header_never_reaches_graph_on_any_legacy_route(monkeypatch):
    client, graph = _client(monkeypatch)
    for path in READS:
        response = client.get(path, headers={
            "X-Synthetic-Proxy-Identity": "synthetic-operator",
        })
        assert response.status_code in (403, 503), (path, response.text)
        assert response.headers.get("cache-control") == "no-store, private"
    assert graph == []


def test_wrong_basic_with_matching_injected_header_is_denied_before_graph(monkeypatch):
    client, graph = _client(monkeypatch)
    for path in READS:
        response = client.get(path,
            headers={"X-Synthetic-Proxy-Identity": "synthetic-operator"},
            auth=("synthetic-operator", "wrong-password"))
        assert response.status_code in (403, 503)
        assert response.headers["cache-control"] == "no-store, private"
    assert graph == []


def test_spoofed_different_principal_with_valid_basic_is_denied(monkeypatch):
    client, graph = _client(monkeypatch)
    for path in (INDEX, EVIDENCE):
        response = client.get(path,
            headers={"X-Synthetic-Proxy-Identity": "spoofed-different-user"},
            auth=("synthetic-operator", "fixture-pw"))
        assert response.status_code == 403
    assert graph == []


def test_missing_or_broken_scope_policy_fails_before_graph(monkeypatch):
    client, graph = _client(monkeypatch)
    for policy, expected in (
        (None, 503),
        (lambda *_: False, 403),
        (lambda *_: (_ for _ in ()).throw(RuntimeError("unavailable")), 403),
    ):
        monkeypatch.setattr(swarm_routes, "_trace_metadata_authorizer", policy)
        for path in (INDEX, EVIDENCE):
            reply=client.get(path, auth=("synthetic-operator", "fixture-pw"))
            assert reply.status_code == expected
            assert reply.headers["cache-control"] == "no-store, private"
    assert graph == []


def test_missing_injected_auth_returns_unavailable_without_fallback(monkeypatch):
    client, graph = _client(monkeypatch)
    monkeypatch.setattr(swarm_routes, "_injected_auth_dependency", None)
    for path in READS:
        reply=client.get(path, auth=("synthetic-operator", "fixture-pw"))
        assert reply.status_code == 503
    assert graph == []


def test_legacy_full_payload_disabled_even_for_operator_basic(monkeypatch):
    client, graph = _client(monkeypatch)
    reply=client.get(DETAIL, auth=("synthetic-operator", "fixture-pw"))
    assert reply.status_code == 503
    assert graph == []


def test_flag_alone_does_not_reenable_full_payload(monkeypatch):
    client, graph = _client(monkeypatch)
    monkeypatch.setenv("ASSISTX_LEGACY_TRACE_DETAIL_ENABLED","1")
    reply=client.get(DETAIL, auth=("synthetic-operator", "fixture-pw"))
    assert reply.status_code == 403
    assert graph == []


def test_legacy_index_and_evidence_allowed_only_with_verified_basic(monkeypatch):
    client, graph = _client(monkeypatch)
    monkeypatch.setattr(swarm_routes, "list_traces",
        lambda _neo, **kw: {"traces":[], "outcome":"all", "total":0})
    monkeypatch.setattr(swarm_routes, "get_trace_task_evidence",
        lambda _neo,_cid: {"match_status":"unverified", "historical_attestation":False})
    for path in (INDEX,EVIDENCE):
        result=client.get(path,auth=("synthetic-operator","fixture-pw"))
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store, private"
    assert len(graph) == 2


def test_legacy_payload_requires_both_flag_and_explicit_permission(monkeypatch):
    client, graph = _client(monkeypatch)
    monkeypatch.setenv("ASSISTX_LEGACY_TRACE_DETAIL_ENABLED","1")
    monkeypatch.setattr(swarm_routes, "_trace_preview_authorizer",
        lambda principal, credentials: basic_preview_permitted(
            principal, credentials,
            configured_user="synthetic-operator",
            configured_password="fixture-pw",
            allowed_users="synthetic-operator"))
    monkeypatch.setattr(swarm_routes, "get_trace",
        lambda _neo, _cid: {"correlation_id":"synthetic-correl",
                              "events":[{"payload_json":"SYNTHETIC_TEST_ONLY"}]})
    reply=client.get(DETAIL,auth=("synthetic-operator","fixture-pw"))
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store, private"
    assert len(graph) == 1


def test_canonical_api_header_priority_cannot_bypass_sensitive_trace_basic(monkeypatch):
    """Exercise real api.auth with invented trusted-header config, no live API."""
    from assistx import api
    monkeypatch.setattr(api, "TRUSTED_AUTH_HEADER", "X-Synthetic-Proxy-Identity")
    monkeypatch.setattr(api, "USER", "synthetic-operator")
    monkeypatch.setattr(api, "PASS", "fixture-pw")
    monkeypatch.setattr(swarm_routes, "_injected_auth_dependency", api.auth)
    monkeypatch.setattr(swarm_routes, "_trace_metadata_authorizer",
        lambda principal, credentials: basic_preview_permitted(
            principal, credentials, configured_user="synthetic-operator",
            configured_password="fixture-pw", allowed_users="synthetic-operator"))
    monkeypatch.setattr(swarm_routes, "_trace_preview_authorizer",
        lambda principal, credentials: basic_preview_permitted(
            principal, credentials, configured_user="synthetic-operator",
            configured_password="fixture-pw", allowed_users=""))  # unavailable by default
    def no_graph():
        raise AssertionError("spoofed header caused graph access")
    monkeypatch.setattr(swarm_routes, "_neo", no_graph)
    app=FastAPI()
    app.include_router(swarm_routes.router)
    client=TestClient(app)
    for path in READS:
        reply=client.get(path,headers={"X-Synthetic-Proxy-Identity":"synthetic-operator"})
        assert reply.status_code in (403,503), (path,reply.text)
        assert reply.headers["cache-control"] == "no-store, private"
        wrong=client.get(path,headers={"X-Synthetic-Proxy-Identity":"synthetic-operator"},
                         auth=("synthetic-operator","incorrect"))
        assert wrong.status_code in (403,503)
    # Valid Basic and the same injected header can authorize only metadata.
    # Full payload remains 503 until both its flag and preview scope are set.
