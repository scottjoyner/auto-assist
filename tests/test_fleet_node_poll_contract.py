"""Fleet worker reads claimed work only with correct scoped and authorized polling."""
from urllib.parse import parse_qs, urlsplit
import pytest
from assistx import fleet_node_agent as agent


def poll(monkeypatch, response, *, caps=None):
    requests, claims, statuses = [], [], []

    def fake_http(method, url, **kwargs):
        requests.append((method, url, kwargs))
        return response

    monkeypatch.setattr(agent, "_http", fake_http)
    monkeypatch.setattr(agent, "_claim_and_run", lambda **kwargs: claims.append(kwargs))
    worked = agent._poll_once(
        assistx_url="http://assistx:8000/",
        router_url="http://router",
        auth=("operator", "test-only-password"),
        node_id="node-a",
        caps=caps if caps is not None else ["llm", "code_execution"],
        lmstudio_url="http://local:1234",
        observed_status=statuses,
    )
    return worked, requests, claims, statuses


def test_exact_backend_query_contract_preserves_auth(monkeypatch):
    task = {"id": "t1", "target_agent_id": "node-a", "required_capabilities": ["llm"]}
    worked, requests, claims, statuses = poll(monkeypatch, (200, {"items": [task]}))
    assert worked and statuses == [200] and len(claims) == 1
    assert claims[0]["task"] is task
    method, url, kwargs = requests[0]
    assert method == "GET"
    assert kwargs["auth"] == ("operator", "test-only-password")
    assert urlsplit(url).path == "/api/agent/tasks"
    assert parse_qs(urlsplit(url).query) == {
        "status": ["READY"], "limit": ["20"], "agent_id": ["node-a"],
        "capabilities": ["code_execution", "llm"],
    }


def test_worker_rejects_wrong_target_bad_caps_and_unfenced_recovery(monkeypatch):
    tasks = [
        {"id": "other", "target_agent_id": "node-b", "required_capabilities": []},
        {"id": "recovery", "required_capabilities": ["recovery"]},
        {"id": "malformed", "required_capabilities": "llm"},
        {"id": "gpu", "required_capabilities": ["gpu"]},
        {"id": "good", "required_capabilities": ["llm"]},
    ]
    worked, requests, claims, _ = poll(monkeypatch, (200, {"items": tasks}),
                                       caps=["llm", "recovery"])
    assert worked
    assert [x["task"]["id"] for x in claims] == ["good"]
    assert parse_qs(urlsplit(requests[0][1]).query)["capabilities"] == ["llm"]
    worked, _, claims, _ = poll(monkeypatch, (200, {"items": tasks[:-1]}),
                                caps=["llm", "recovery"])
    assert not worked and not claims


@pytest.mark.parametrize("code", [0, 401, 403, 429, 500, 503])
def test_non_success_polls_never_claim(monkeypatch, code):
    worked, _, claims, statuses = poll(monkeypatch, (code, {"detail": "blocked"}))
    assert not worked and claims == [] and statuses == [code]


def test_empty_model_listener_is_not_an_llm_capability(monkeypatch):
    monkeypatch.setattr(agent.subprocess, "run",
        lambda *a, **kw: type("Process", (), {"returncode": 1})())
    monkeypatch.delenv("FLEET_CAPABILITIES", raising=False)
    monkeypatch.setattr(agent, "_http", lambda *a, **kw: (200, {"data": []}))
    caps, models = agent._detect_capabilities("http://local")
    assert not models and "llm" not in caps and "lmstudio" not in caps
    monkeypatch.setattr(agent, "_http",
        lambda *a, **kw: (200, {"data": [{"id": "model-a"}]}))
    caps, models = agent._detect_capabilities("http://local")
    assert models == ["model-a"] and "llm" in caps


def test_unauthorized_backoff_resets_on_non_auth_response(monkeypatch):
    sleeps = []
    monkeypatch.setattr(agent, "_detect_hostname", lambda: "node-a")
    monkeypatch.setattr(agent, "_auth_from_env", lambda: ("operator", "password"))
    monkeypatch.setattr(agent, "_detect_capabilities", lambda _: (["llm"], ["model-a"]))
    monkeypatch.setattr(agent, "_http", lambda *a, **kw: (200, {}))

    def fake_poll(**kwargs):
        kwargs["observed_status"].append([401, 403, 200, 401][len(sleeps)])
        return False

    monkeypatch.setattr(agent, "_poll_once", fake_poll)

    def fake_sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 4:
            raise StopIteration("finished isolated test")

    monkeypatch.setattr(agent.time, "sleep", fake_sleep)
    with pytest.raises(StopIteration):
        agent.run(assistx_url="http://assistx", router_url="http://router",
                  poll_interval=5, concurrency=1, lmstudio_url="http://local")
    assert sleeps == [10.0, 20.0, 5.0, 10.0]


def test_rejected_claim_yields_instead_of_hot_loop(monkeypatch):
    attempted = []
    monkeypatch.setattr(agent, "_http", lambda *a, **kw: (200, {"items": [
        {"id": "still-ready", "required_capabilities": ["llm"]}]}))
    monkeypatch.setattr(agent, "_claim_and_run",
                        lambda **kw: (attempted.append(kw) or False))
    statuses = []
    worked = agent._poll_once(
        assistx_url="http://assistx", router_url="http://router",
        auth=("operator", "test"), node_id="node-a",
        caps=["llm"], lmstudio_url="http://local", observed_status=statuses)
    assert statuses == [200] and not worked and len(attempted) == 1
