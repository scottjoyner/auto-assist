"""Legacy Basic-auth worker must never claim or execute recovery runbooks.

All inputs, services and HTTP results are synthetic. No API/task writes.
"""
import json

import pytest
from assistx import fleet_node_agent as agent


RECOVERY_READY = [
    {"id": "script-task", "task_type": "script",
     "required_capabilities": ["llm"]},
    {"id": "typed-recovery", "task_type": "RECOVERY"},
    {"id": "runbook-dict", "payload": {"runbook": {"action": "health_check"}},
     "required_capabilities": ["llm"]},
    {"id": "runbook-json", "payload_json": '{"runbook":{"action":"health_check"}}',
     "required_capabilities": ["llm"]},
    {"id": "upper-cap", "required_capabilities": ["Recovery", "llm"]},
    {"id": "bad-json", "payload_json": '{"runbook":'},
    {"id": "dup-json", "payload_json": '{"runbook":1,"runbook":null}'},
    {"id": "strange-payload", "payload": ["not an object"]},
]


@pytest.mark.parametrize("task", RECOVERY_READY)
def test_ready_list_never_claims_recovery_without_token(monkeypatch, task):
    requests = []
    def fake_http(method, url, **kwargs):
        requests.append(method)
        if method != "GET":
            raise AssertionError("legacy recovery must never be claimed")
        return 200, {"items": [task]}

    monkeypatch.setattr(agent, "_http", fake_http)
    monkeypatch.setattr(agent, "_claim_and_run", lambda **kwargs:
                        (_ for _ in ()).throw(AssertionError("recovery dispatch")))
    statuses = []
    result = agent._poll_once(
        assistx_url="http://synthetic", router_url="http://router",
        auth=("test-user", "fake-value"), node_id="node-a",
        caps=["llm", "script", "recovery"], lmstudio_url="http://local",
        observed_status=statuses,
    )
    assert result is False and statuses == [200]
    assert requests == ["GET"]


@pytest.mark.parametrize("task", RECOVERY_READY)
def test_direct_claim_refuses_recovery_before_http(monkeypatch, task):
    monkeypatch.setattr(agent, "_http", lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError("no HTTP allowed")))
    assert agent._claim_and_run(
        assistx_url="http://synthetic", router_url="http://router",
        auth=None, node_id="node-a", caps=["llm", "recovery"],
        task=task, lmstudio_url="http://local",
    ) is False

@pytest.mark.parametrize("claimed", [
    {"task_type": "script", "payload": {"runbook": {"action": "health_check"}}},
    {"task_type": "llm", "required_capabilities": ["recovery"]},
    {"task_type": "llm", "payload_json": '{"runbook":{"action":"health_check"}}'},
])
def test_authoritative_claim_recovery_denied_before_execution(monkeypatch, claimed):
    calls = []

    def fake_http(method, url, **kwargs):
        calls.append((method, url))
        if url.endswith("/claim"):
            return 200, {"claimed": True, "task": {
                "id": "task-1", "claimed_by": "node-a", "status": "CLAIMED",
                "agent_session_id": kwargs["data"]["session_id"],
                "claim_id": "fence-test", **claimed}}
        raise AssertionError("no heartbeat/model/runbook/complete allowed")

    monkeypatch.setattr(agent, "_http", fake_http)
    monkeypatch.setattr(agent, "RecoveryRunbookExecutor", lambda **kw:
                        (_ for _ in ()).throw(AssertionError("runbook started")))
    assert agent._claim_and_run(
        assistx_url="http://synthetic", router_url="http://router",
        auth=None, node_id="node-a", caps=["llm", "recovery"],
        task={"id": "task-1", "task_type": "llm",
              "payload": {"prompt": "review only"}},
        lmstudio_url="http://local") is False
    assert len(calls) == 1 and calls[0][1].endswith("/claim")

def test_legacy_claim_never_advertises_recovery_capability(monkeypatch):
    calls = []

    def fake_http(method, url, **kwargs):
        calls.append(kwargs.get("data"))
        return 409, {"claimed": False}

    monkeypatch.setattr(agent, "_http", fake_http)
    assert agent._claim_and_run(
        assistx_url="http://synthetic", router_url="http://router",
        auth=None, node_id="node-a", caps=["llm", "Recovery", "recovery"],
        task={"id": "ordinary", "task_type": "llm"},
        lmstudio_url="http://local") is False
    assert calls == [{"agent_id": "node-a", "capabilities": ["llm"],
                      "session_id": calls[0]["session_id"], "lease_seconds": 900}]


def test_review_only_llm_envelope_remains_eligible_without_exec():
    task = {"id": "good", "task_type": "llm",
            "required_capabilities": ["llm"],
            "payload_json": json.dumps({"prompt": "analyze code"})}
    assert agent._legacy_worker_recovery_denied(task) is False
    assert agent._legacy_worker_recovery_denied(
        {**task, "payload_json": '{"prompt":"x","prompt":"y"}'}) is True
