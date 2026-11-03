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


def test_real_claim_http_409_never_executes_or_reports_completion(monkeypatch):
    events = []

    def fake_http(method, url, **kwargs):
        events.append((method, url))
        if method == "GET" and "/api/agent/tasks?" in url:
            return 200, {"items": [
                {"id": "unclaimable", "task_type": "llm",
                 "required_capabilities": ["llm"]},
            ]}
        if method == "POST" and url.endswith("/api/tasks/unclaimable/claim"):
            return 409, {"claimed": False}
        raise AssertionError("No heartbeat, model call, or completion after 409")

    monkeypatch.setattr(agent, "_http", fake_http)
    observed = []
    worked = agent._poll_once(
        assistx_url="http://assistx", router_url="http://router",
        auth=("operator", "test"), node_id="node-a", caps=["llm"],
        lmstudio_url="http://local", observed_status=observed)
    assert worked is False and observed == [200]
    assert len(events) == 2
    assert events[1][1].endswith("/api/tasks/unclaimable/claim")


@pytest.mark.parametrize("claim", [
    {"claimed": True, "task": {"id": "claimable", "claimed_by": "node-a", "status": "CLAIMED"}},
    {"claimed": True, "claim_id": "mismatch",
     "task": {"id": "claimable", "claimed_by": "node-a", "status": "CLAIMED", "claim_id": "real"}},
    {"claimed": True, "task": {"id": "other", "claimed_by": "node-a", "status": "CLAIMED", "claim_id": "real"}},
    {"claimed": True, "task": {"id": "claimable", "claimed_by": "other", "status": "CLAIMED", "claim_id": "real"}},
    {"claimed": True, "task": {"id": "claimable", "claimed_by": "node-a", "status": "READY", "claim_id": "real"}},
    {"claimed": False, "task": {"id": "claimable", "claimed_by": "node-a", "status": "CLAIMED", "claim_id": "real"}},
])
def test_malformed_successful_claim_never_runs_or_heartbeats(monkeypatch, claim):
    calls = []

    def fake_http(method, url, **kwargs):
        calls.append((method, url))
        if url.endswith("/claim"):
            return 200, claim
        raise AssertionError("Unexpected inference, heartbeat, or completion")

    monkeypatch.setattr(agent, "_http", fake_http)
    result = agent._claim_and_run(
        assistx_url="http://assistx", router_url="http://router",
        auth=("operator", "test"), node_id="node-a",
        caps=["llm"], task={"id": "claimable", "required_capabilities": ["llm"]},
        lmstudio_url="http://local")
    assert result is False and len(calls) == 1


def test_valid_claim_fence_flows_into_completion(monkeypatch):
    requests = []

    def fake_http(method, url, **kwargs):
        requests.append((method, url, kwargs))
        if url.endswith("/claim"):
            return 200, {"claimed": True, "task": {
                "id": "claimable", "claimed_by": "node-a", "status": "CLAIMED",
                "claim_id": "claim-fenced-123", "task_type": "llm",
                "payload_json": '{"prompt":"review","model":"m1"}',
                "agent_session_id": kwargs["data"]["session_id"]}}
        if url.endswith("/v1/chat/completions"):
            return 200, {"choices": [{"message": {"content": "review successful"}}]}
        if url.endswith("/complete"):
            return 200, {"task": {"id": "claimable", "status": "DONE"}}
        raise AssertionError(f"Unexpected route: {url}")

    monkeypatch.setattr(agent, "_http", fake_http)
    outcome = agent._claim_and_run(
        assistx_url="http://assistx", router_url="http://router",
        auth=("operator", "test"), node_id="node-a", caps=["llm"],
        task={"id": "claimable", "task_type": "llm",
              "payload": {"prompt": "review", "model": "m1"}},
        lmstudio_url="http://local")
    assert outcome is None
    completion = [r for r in requests if r[1].endswith("/complete")]
    assert len(completion) == 1
    assert completion[0][2]["data"]["claim_id"] == "claim-fenced-123"
    assert len([r for r in requests if "/v1/chat/completions" in r[1]]) == 1


def test_http_422_claim_denial_never_dispatches(monkeypatch):
    requests = []

    def fake_http(method, url, **kwargs):
        requests.append((method, url))
        if method == "GET" and "/api/agent/tasks?" in url:
            return 200, {"items": [{
                "id": "malformed", "required_capabilities": ["llm"],
                "task_type": "llm"}]}
        if method == "POST" and url.endswith("/api/tasks/malformed/claim"):
            return 422, {"detail": "invalid claim"}
        raise AssertionError("No model request, heartbeat or completion allowed")

    monkeypatch.setattr(agent, "_http", fake_http)
    status = []
    worked = agent._poll_once(
        assistx_url="http://assistx", router_url="http://router",
        auth=("operator", "test"), node_id="node-a", caps=["llm"],
        lmstudio_url="http://local", observed_status=status)
    assert worked is False
    assert status == [200]
    assert len(requests) == 2

def test_execution_uses_authoritative_claimed_payload_not_stale_ready_entry(monkeypatch):
    emitted = []

    def fake_http(method, url, **kwargs):
        if url.endswith("/claim"):
            return 200, {"claimed": True, "task": {
                "id": "claimable", "claimed_by": "node-a", "status": "CLAIMED",
                "agent_session_id": kwargs["data"]["session_id"],
                "claim_id": "fence-123", "task_type": "llm",
                "payload_json": '{"prompt":"CURRENT AUTHORIZED REVIEW","model":"model-a"}'}}
        if url.endswith("/v1/chat/completions"):
            emitted.append(kwargs["data"])
            return 200, {"choices": [{"message": {"content": "done"}}]}
        if url.endswith("/complete"):
            return 200, {"task": {"id": "claimable", "status": "DONE"}}
        raise AssertionError("Unexpected endpoint")

    monkeypatch.setattr(agent, "_http", fake_http)
    outcome = agent._claim_and_run(
        assistx_url="http://assistx", router_url="http://router",
        auth=("operator", "test"), node_id="node-a", caps=["llm"],
        task={"id": "claimable", "task_type": "llm",
              "payload": {"prompt": "STALE UNAUTHORIZED REQUEST", "model": "old"}},
        lmstudio_url="http://local")
    assert outcome is None
    assert emitted == [{
        "model": "model-a",
        "messages": [{"role": "user", "content": "CURRENT AUTHORIZED REVIEW"}],
        "stream": False,
    }]


@pytest.mark.parametrize("body", [
    {},
    {"choices": []},
    {"choices": [{}]},
    {"choices": [{"message": {}}]},
    {"choices": [{"message": {"content": ""}}]},
    {"choices": [{"message": {"content": "  \n"}}]},
    {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": []}}]},
])
def test_http_200_without_generated_answer_must_not_complete_done(monkeypatch, body):
    calls = []

    def fake_http(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/claim"):
            return 200, {"claimed": True, "task": {
                "id": "claimable", "claimed_by": "node-a", "status": "CLAIMED",
                "agent_session_id": kwargs["data"]["session_id"],
                "claim_id": "fence-123", "task_type": "llm",
                "payload_json": '{"prompt":"Review a PR","model":"m1"}'}}
        if url.endswith("/v1/chat/completions"):
            return 200, body
        if url.endswith("/complete"):
            return 200, {"task": {"id": "claimable", "status": "FAILED"}}
        raise AssertionError("Unexpected route")

    monkeypatch.setattr(agent, "_http", fake_http)
    outcome = agent._claim_and_run(
        assistx_url="http://assistx", router_url="http://router",
        auth=("operator", "test"), node_id="node-a", caps=["llm"],
        task={"id": "claimable", "required_capabilities": ["llm"]},
        lmstudio_url="http://local")
    assert outcome is None
    completions = [entry[2]["data"] for entry in calls if entry[1].endswith("/complete")]
    assert len(completions) == 1
    assert completions[0]["status"] == "FAILED"
    assert completions[0]["result"]["error"] == "empty_or_invalid_model_generation"
    assert completions[0]["claim_id"] == "fence-123"

@pytest.mark.parametrize("code,ack", [
    (0, {}),
    (409, {"detail": "stale claim"}),
    (200, {}),
    (200, {"task": {"id": "wrong", "status": "DONE"}}),
    (200, {"task": {"id": "claimable", "status": "CLAIMED"}}),
])
def test_unacknowledged_completion_does_not_count_as_work(monkeypatch, code, ack):
    requests = []

    def fake_http(method, url, **kwargs):
        requests.append((method, url, kwargs))
        if url.endswith("/claim"):
            return 200, {"claimed": True, "task": {
                "id": "claimable", "claimed_by": "node-a", "status": "CLAIMED",
                "agent_session_id": kwargs["data"]["session_id"],
                "claim_id": "fence-1", "task_type": "llm",
                "payload_json": '{"prompt":"Audit work supply","model":"m1"}'}}
        if url.endswith("/v1/chat/completions"):
            return 200, {"choices": [{"message": {"content": "Audit complete"}}]}
        if url.endswith("/complete"):
            return code, ack
        raise AssertionError("Unexpected request")

    monkeypatch.setattr(agent, "_http", fake_http)
    result = agent._claim_and_run(
        assistx_url="http://assistx", router_url="http://router",
        auth=("operator", "test"), node_id="node-a", caps=["llm"],
        task={"id": "claimable"}, lmstudio_url="http://local")
    assert result is False
    assert len([x for x in requests if x[1].endswith("/v1/chat/completions")]) == 1
    assert len([x for x in requests if x[1].endswith("/complete")]) == 1
