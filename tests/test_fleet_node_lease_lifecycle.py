"""Synthetic lease lifecycle regressions; all requests are mocked."""
import threading
import time

import pytest
from assistx import fleet_node_agent as agent


def run_worker(monkeypatch, *, fail_at=0, failure=None, terminal_changes=None,
               periodic=False, benchmark=False):
    calls = []
    counter = 0
    failed = threading.Event()
    claim = {}
    payload = {"prompt": "synthetic review", "model": "m1"}
    if benchmark:
        payload.update(benchmark=True, cases=[{"prompt": "one"}, {"prompt": "two"}])

    def fake_http(method, url, **kwargs):
        nonlocal counter
        kind = url.rsplit("/", 1)[-1]
        calls.append(kind)
        data = kwargs["data"]
        if kind == "claim":
            claim.update(id="t1", claimed_by="node-a", claim_id="c1",
                         agent_session_id=data["session_id"], status="CLAIMED",
                         task_type="llm", payload=payload)
            return 200, {"claimed": True, "task": dict(claim)}
        if kind == "heartbeat":
            counter += 1
            if counter == fail_at:
                failed.set()
                code, changes = failure
                return code, {"task": {**claim, "status": "RUNNING",
                    "lease_expires_at_ts": int(time.time()*1000)+900000, **changes}}
            return 200, {"task": {**claim, "status": "RUNNING",
                    "lease_expires_at_ts": int(time.time()*1000)+900000}}
        if kind == "completions":
            if periodic:
                failed.wait(2)
                # Ensure the heartbeat thread has consumed its synthetic denial.
                time.sleep(0.03)
            return 200, {"choices": [{"message": {"content": "answer"}}]}
        if kind == "complete":
            return 200, {"task": {**claim, "status": data["status"],
                "completed_by": "node-a", "run_id": "r1", **(terminal_changes or {})}}
        raise AssertionError(kind)

    monkeypatch.setattr(agent, "_http", fake_http)
    if periodic:
        monkeypatch.setattr(agent, "_LEASE_HEARTBEAT_INTERVAL", 0.005, raising=False)
    result = agent._claim_and_run(assistx_url="http://synthetic", router_url="http://router",
        auth=("test", "fake"), node_id="node-a", caps=["llm"],
        task={"id": "t1", "task_type": "llm"}, lmstudio_url="http://local")
    return result, calls


@pytest.mark.parametrize("failure", [
    (401, {}), (403, {}), (409, {}), (0, {}),
    (200, {"claim_id": "other"}), (200, {"claimed_by": "other"}),
    (200, {"agent_session_id": "other"}), (200, {"id": "other"}),
    (200, {"status": "PAUSING"}), (200, {"lease_expires_at_ts": 0}),
    (200, {"lease_expires_at_ts": True}),
])
def test_initial_denial_prevents_inference_and_completion(monkeypatch, failure):
    result, calls = run_worker(monkeypatch, fail_at=1, failure=failure)
    assert result is False
    assert calls == ["claim", "heartbeat"]


def test_final_lease_denial_discards_inference_result(monkeypatch):
    result, calls = run_worker(monkeypatch, fail_at=2, failure=(409, {}))
    assert result is False
    assert calls == ["claim", "heartbeat", "completions", "heartbeat"]


def test_periodic_denial_blocks_completion_and_next_benchmark_case(monkeypatch):
    result, calls = run_worker(monkeypatch, fail_at=2, failure=(403, {}),
                               periodic=True, benchmark=True)
    assert result is False
    assert calls.count("completions") == 1
    assert "complete" not in calls


@pytest.mark.parametrize("changes", [{"claim_id": "wrong"}, {"completed_by": "other"},
    {"agent_session_id": "wrong"}, {"run_id": ""}])
def test_completion_receipt_requires_exact_fence(monkeypatch, changes):
    result, calls = run_worker(monkeypatch, terminal_changes=changes)
    assert result is False
    assert calls.count("complete") == 1


def test_success_requires_initial_final_heartbeat_and_fenced_ack(monkeypatch):
    result, calls = run_worker(monkeypatch)
    assert result is None
    assert calls == ["claim", "heartbeat", "completions", "heartbeat", "complete"]

