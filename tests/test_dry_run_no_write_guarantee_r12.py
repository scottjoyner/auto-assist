"""Hermetic regression sentinels for AssistX ``dry_run`` (preview) execution (r12).

Scope: the core execution seam ``agents.orchestrator.run_task`` and its two
callers (``jobs.execute_task_job``, ``pipeline_execute.execute_ready``).  Before
this file, no test imported any of those modules; ``dry_run`` was only pinned at
the unrelated ``scripts/assistx-fleet-admit-live.py`` invocation gate.

Everything here is in-memory: a recording graph fake stands in for Neo4j, the
LLM decision is a deterministic fake, and tool functions are replaced with
recorders.  No LLM, network, Docker, Neo4j, outbox DB or filesystem write is
performed.

Current fail-open (documented, not introduced here): ``dry_run=True`` still
creates/complets Run nodes, logs tool calls, writes Task statuses and can
dispatch side-effecting tools.  The desired no-write invariant is pinned with
``strict=True`` xfails so it remains visible as a known gap without adding a
hard failure; removing an xfail is the signal that the matching source fix has
landed.
"""

from __future__ import annotations

import pytest

from assistx import jobs as jobs_mod
from assistx import pipeline_execute as pipeline_mod
from assistx.agents import orchestrator as orch

_RUN_ID = "run-r12-synthetic"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def run(self, _cypher, _params=None):
        return _Result(self._rows)


class RecordingNeo:
    """Records only; every mutation is observable and nothing leaves memory."""

    def __init__(self, tasks=None):
        self.calls = []
        self.tasks = list(tasks or [])

    def create_run(self, **kwargs):
        self.calls.append(("create_run", kwargs))
        return _RUN_ID

    def log_tool_call(self, *args, **kwargs):
        self.calls.append(("log_tool_call", args, kwargs))

    def complete_run(self, *args, **kwargs):
        self.calls.append(("complete_run", args, kwargs))

    def log_artifact(self, *args, **kwargs):
        self.calls.append(("log_artifact", args, kwargs))

    def update_task_status(self, *args, **kwargs):
        self.calls.append(("update_task_status", args, kwargs))

    def get_ready_tasks(self, limit=5):
        return list(self.tasks[:limit])

    def close(self):
        self.calls.append(("close",))

    def _session(self):
        # Emulate a Neo4j Record: rec[0] is the bound task node. The previous
        # {"t": ...} shape raised KeyError before any assertion ran, so the
        # job-path sentinels never actually exercised execute_task_job.
        return _Session([[dict(t)] for t in self.tasks])

    def names(self):
        return [c[0] for c in self.calls]


def _fake_decide(state):
    state.result = {"summary": "preview-only"}
    state.done = True
    return state


@pytest.fixture
def safe_decide(monkeypatch):
    """A decision step with no LLM and no tool dispatch."""
    monkeypatch.setattr(orch, "decide", _fake_decide)
    monkeypatch.setattr(orch, "last_reasoning_content", lambda: None)


def test_dry_run_preview_uses_single_decision_and_skips_graph_invoke(monkeypatch, safe_decide):
    class _NeverInvoke:
        def invoke(self, *_a, **_k):
            pytest.fail("dry_run must not enter the multi-step graph loop")

    monkeypatch.setattr(orch, "build_graph", lambda: _NeverInvoke())
    neo = RecordingNeo()
    out = orch.run_task(neo, {"id": "t1", "model": "synthetic"}, dry_run=True)
    assert out["done"] is True
    assert out["result"] == {"summary": "preview-only"}
    # dry_run no longer creates an AgentRun node; the id is a local preview label.
    assert out["run_id"] == orch._DRY_RUN_RUN_ID


def test_dry_run_final_decision_does_not_dispatch_tools(monkeypatch):
    dispatched = []

    def _record(name):
        def _func(args):
            dispatched.append((name, args))

        return _func

    for name in ("web_search", "python", "write_text"):
        monkeypatch.setitem(orch.TOOLS[name], "func", _record(name))
    monkeypatch.setattr(orch, "json_chat", lambda *_a, **_k: {"final": {"result": {"ok": True}}})
    monkeypatch.setattr(orch, "last_reasoning_content", lambda: None)

    neo = RecordingNeo()
    out = orch.run_task(neo, {"id": "t2"}, dry_run=True)
    assert out["result"] == {"result": {"ok": True}}
    assert dispatched == []


def test_dry_run_malformed_decision_fails_closed_without_tools(monkeypatch):
    dispatched = []
    for name in ("web_search", "python", "write_text"):
        monkeypatch.setitem(orch.TOOLS[name], "func", (lambda n: lambda _a: dispatched.append(n))(name))
    monkeypatch.setattr(orch, "json_chat", lambda *_a, **_k: "not-an-object")
    monkeypatch.setattr(orch, "last_reasoning_content", lambda: None)

    neo = RecordingNeo()
    out = orch.run_task(neo, {"id": "t3"}, dry_run=True)
    assert out["done"] is True
    assert any("malformed decision" in str(step) for step in out["history"])
    assert dispatched == []


def test_dry_run_must_not_mutate_graph_state(safe_decide):
    neo = RecordingNeo()
    orch.run_task(neo, {"id": "t4"}, dry_run=True)
    assert neo.calls == []


def test_dry_run_must_not_dispatch_side_effecting_tools(monkeypatch):
    dispatched = []
    for name in ("web_search", "python", "write_text"):
        monkeypatch.setitem(orch.TOOLS[name], "func", (lambda n: lambda _a: dispatched.append(n))(name))
    monkeypatch.setattr(
        orch,
        "json_chat",
        lambda *_a, **_k: {"tool": "write_text", "input": {"path": "/never/touched", "text": "x"}},
    )
    monkeypatch.setattr(orch, "last_reasoning_content", lambda: None)

    neo = RecordingNeo()
    orch.run_task(neo, {"id": "t5"}, dry_run=True)
    assert dispatched == []


def _run_job(monkeypatch, neo, dry_run=True, passed=False):
    monkeypatch.setattr(jobs_mod, "Neo4jClient", lambda: neo)
    monkeypatch.setattr(jobs_mod, "run_task", lambda _neo, _task, dry_run=False: {"run_id": _RUN_ID})
    monkeypatch.setattr(jobs_mod, "evaluate_acceptance", lambda *_a, **_k: {"passed": passed})
    return jobs_mod.execute_task_job("t6", dry_run=dry_run)


def test_dry_run_job_must_not_write_task_status(monkeypatch):
    neo = RecordingNeo(tasks=[{"id": "t6", "model": "synthetic"}])
    result = _run_job(monkeypatch, neo, dry_run=True)
    # dry_run does not force DONE: with failed acceptance the job reports FAILED.
    assert result["status"] == "FAILED"
    assert "update_task_status" not in neo.names()


def test_dry_run_job_must_not_force_done_when_acceptance_fails(monkeypatch):
    neo = RecordingNeo(tasks=[{"id": "t6", "model": "synthetic"}])
    result = _run_job(monkeypatch, neo, dry_run=True, passed=False)
    assert result["status"] == "FAILED"


def test_dry_run_execute_ready_must_not_write_task_status(monkeypatch):
    neo = RecordingNeo(tasks=[{"id": "t7", "model": "synthetic"}])
    monkeypatch.setattr(pipeline_mod, "run_task", lambda _neo, _task, dry_run=False: {"run_id": _RUN_ID})
    monkeypatch.setattr(pipeline_mod, "evaluate_acceptance", lambda *_a, **_k: {"passed": False})
    pipeline_mod.execute_ready(neo, dry_run=True)
    assert "update_task_status" not in neo.names()
