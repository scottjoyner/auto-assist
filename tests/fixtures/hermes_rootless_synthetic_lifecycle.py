"""No-network, disposable ONLY: real strict main, synthetic claim flow, fake engine."""

import os
import pathlib
import sys
import subprocess
from unittest.mock import patch
sys.path.insert(0, "/app/src")
from assistx import strict_executor_main as entry
agent = entry.hermes_agent_adapter
task_id = "synthetic-hermes-task-001"
claim_id = "synthetic-claim-001"
agent_id = "hermes-synthetic-canary"
task = {
    "id": task_id,
    "title": "Fix synthetic local test marker",
    "description": "Synthetic request; zero real changes",
    "payload": {"model": "synthetic/tool-model"},
}
events = []
fail_token_identity = [False]
engine_calls = []
class FakeResponse:
    def __init__(self, body): self.body = body
    def raise_for_status(self): return None
    def json(self): return self.body
def http(method, url, **kw):
    assert url.startswith("http://127.0.0.1:1/"), "unexpected external HTTP"
    path = url.removeprefix("http://127.0.0.1:1")
    auth = kw.get("headers", {}).get("Authorization")
    assert kw.get("headers", {}).get("X-AssistX-Agent-Id") == agent_id
    boot = path.startswith("/api/agent/tasks") or path.endswith("/claim") or path.startswith("/api/executor/claims/")
    assert auth == ("Bearer SYNTHETIC_BOOTSTRAP_ONLY" if boot else "Bearer SYNTHETIC_CLAIM_ONLY")
    if method == "GET" and path.startswith("/api/agent/tasks?"):
        event = "poll"; body = {"items": [dict(task)]}
    elif method == "POST" and path == "/api/tasks/" + task_id + "/claim":
        event = "claim"; body = {"claimed": True, "task": {"claim_id": claim_id}}
    elif method == "POST" and path == "/api/executor/claims/" + task_id + "/token":
        event = "token"
        body = {"token": "SYNTHETIC_CLAIM_ONLY",
                "claims": {"task_id": task_id,
                           "claim_id": "wrong-claim" if fail_token_identity[0] else claim_id,
                           "agent_id": agent_id}}
    elif method == "POST" and path == "/api/tasks/" + task_id + "/heartbeat":
        event = "heartbeat"; body = {}
        assert kw.get("json", {}).get("claim_id") == claim_id
    elif method == "POST" and path == "/api/brain/context":
        event = "context"; body = {"context_packet": {"references": []}}
    elif method == "POST" and path == "/api/memory/items":
        event = "memory"; body = {"memory_item_id": "synthetic-memory-only"}
    elif method == "POST" and path == "/api/tasks/" + task_id + "/complete":
        event = "complete"; body = {}
        assert kw.get("json", {}).get("status") == "DONE"
        assert kw.get("json", {}).get("claim_id") == claim_id
    else:
        raise AssertionError("unapproved mock endpoint or method")
    events.append(event)
    return FakeResponse(body)
def fake_engine(*args, **kwargs):
    engine_calls.append(1)
    assert kwargs.get("model") == "synthetic/tool-model"
    return {"success": True, "output": "SYNTHETIC_ONLY: meaningful fake task result and bounded summary",
            "session_id": None, "elapsed": 0.01}
def forbid_process(*args, **kwargs):
    raise AssertionError("Hermes must not spawn any executable in mock lifecycle")
assert os.geteuid() != 0
assert pathlib.Path("/workspace/hermes-home/config.yaml").is_file()
assert pathlib.Path("/workspace/.hermes/config.yaml").is_symlink()
assert not pathlib.Path("/workspace/.hermes/.env").exists()
assert pathlib.Path("/research/knowledge/synthetic-source.txt").read_text().strip() == "SYNTHETIC_ONLY"
for denied in ("/media/scott/SSD_4TB", "/media/scott/NAS5", "/root/knowledge"):
    try: assert not pathlib.Path(denied).exists(), denied
    except PermissionError: pass
try:
    pathlib.Path("/research/knowledge/forbidden").write_text("NO")
    raise AssertionError("readonly knowledge unexpectedly writable")
except OSError: pass
try:
    pathlib.Path("/etc/hermes-lifecycle-denied").write_text("NO")
    raise AssertionError("rootfs unexpectedly writable")
except OSError: pass
stat = pathlib.Path("/proc/self/status").read_text()
assert "NoNewPrivs:\t1" in stat and "CapEff:\t0000000000000000" in stat
with patch("requests.request", side_effect=http), \
     patch.object(subprocess, "Popen", side_effect=forbid_process), \
     patch.object(agent, "fleet", None), \
     patch.object(agent, "SELFTASK_BULK_MODELS", []), \
     patch.object(agent, "HERMES_DELEGATE_OPENCODE_TIERS", []):
    original_run_loop = agent.run_loop
    assert not getattr(agent, "_strict_executor_auth_installed", False)
    def bounded_single_poll():
        # Entered from ACTUAL entry.main() *after* strict auth installation.
        assert getattr(agent, "_strict_executor_auth_installed", False)
        assert agent.run_hermes("synthetic")["error"] == "executor_task_token_missing"
        with patch.object(agent, "run_hermes", side_effect=fake_engine):
            original_run_loop(once=True)
    with patch.object(agent, "run_loop", side_effect=bounded_single_poll):
        entry.main()
    expected = ["poll", "claim", "token", "heartbeat", "context",
                "heartbeat", "memory", "complete"]
    assert events == expected, (events, expected)
    assert len(engine_calls) == 1
    assert pathlib.Path("/workspace/registry/model-profiles.json").exists()
    assert pathlib.Path("/workspace/knowledge/synthetic_tool-model/ENV.md").exists()
    # Completing the task cleared claim capability. New claim + revoke must
    # prohibit authoritative completion even if the worker asks for DONE.
    client = agent.AssistXClient()
    claim = client.claim_task(task_id, "synthetic-session-revoked")
    assert claim["claimed"]
    assert client.task_token == "SYNTHETIC_CLAIM_ONLY"
    pre_complete = events.count("complete")
    client._lease_lost.set()
    client.complete_task(task_id, "synthetic-session-revoked",
                         "DONE", claim_id=claim_id)
    assert events.count("complete") == pre_complete
    assert client.task_token == ""
    fail_token_identity[0] = True
    try:
        agent.AssistXClient().claim_task(task_id, "synthetic-invalid-claim")
        raise AssertionError("mismatched claim identity was accepted")
    except RuntimeError as e:
        assert "different claim identity" in str(e)
    assert events.count("complete") == pre_complete
    assert len(engine_calls) == 1
assert "SYNTHETIC_ONLY" in pathlib.Path("/workspace/knowledge/synthetic_tool-model/ENV.md").read_text() or pathlib.Path("/workspace/knowledge/synthetic_tool-model/ENV.md").is_file()
print("ROOTLESS_HERMES_MAIN_SYNTHETIC_LIFECYCLE_PASS")
