"""Exercise old API/job/CLI pipeline entrypoints with no imported app or graph.

Only the exact source function AST is compiled in a fake namespace. This
ensures a denied database transition cannot dispatch model/task work, even
when the surrounding production API has optional dependencies.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "assistx"


class Denied(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


class Result:
    def single(self):
        return {0: {"id": "synthetic-only", "status": "READY"}}


class Session:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def run(self, *args, **kwargs):
        return Result()


class FakeNeo:
    def __init__(self, transitions):
        self.transitions = list(transitions)
        self.calls = []
        self.closed = False

    def _session(self):
        return Session()

    def update_task_status(self, tid, status):
        self.calls.append(("status", tid, status))
        return self.transitions.pop(0)

    def close(self):
        self.closed = True

    def get_ready_tasks(self, limit=5):
        return [{"id": "synthetic-only", "status": "READY"}]

    def log_tool_call(self, *args):
        self.calls.append(("tool",))


class Log:
    def __init__(self):
        self.events = []

    def warning(self, message, *args):
        self.events.append(("warn", message))

    def error(self, message, *args):
        self.events.append(("error", message))

    def info(self, message, *args):
        self.events.append(("info", message))


def function_from_source(filename, name, env):
    source = ast.parse((ROOT / filename).read_text())
    methods = [n for n in source.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == name]
    assert len(methods) == 1
    node = methods[0]
    node.decorator_list = []
    code = compile(ast.fix_missing_locations(ast.Module(
        body=[node], type_ignores=[])), filename, "exec")
    space = dict(env)
    exec(code, space)
    return space[name]


def make_env(neo, calls, log=None):
    def run_task(db, task, dry_run=False):
        calls.append("MODEL_OR_TASK_EXECUTION")
        return {"run_id": None, "result": True}

    return {
        "_neo": lambda: neo,
        "Neo4jClient": lambda: neo,
        "HTTPException": Denied,
        "Depends": lambda dependency: None,
        "auth": lambda: None,
        "RedirectResponse": lambda **kw: kw,
        "JSONResponse": lambda body, status_code=200: {
            "body": body, "status_code": status_code,
        },
        "run_task": run_task,
        "_json_safe": lambda value: value,
        "evaluate_acceptance": lambda *args: {"passed": True},
        "logger": log or Log(),
    }


def test_api_rejects_task_before_model_work():
    neo, calls = FakeNeo([False]), []
    f = function_from_source("api.py", "execute_task", make_env(neo, calls))
    with pytest.raises(Denied) as denial:
        f("synthetic-only", user="synthetic")
    assert denial.value.status_code == 409
    assert calls == []
    assert neo.closed


def test_api_does_not_announce_done_if_terminal_status_uncommitted():
    neo, calls = FakeNeo([True, False]), []
    f = function_from_source("api.py", "execute_task", make_env(neo, calls))
    outcome = f("synthetic-only", user="synthetic")
    assert calls == ["MODEL_OR_TASK_EXECUTION"]
    assert outcome["status_code"] == 409
    assert outcome["body"]["status"] == "HOLD"
    assert neo.closed


def test_approve_fails_closed_without_reporting_success():
    neo, calls = FakeNeo([False]), []
    f = function_from_source("api.py", "approve_task", make_env(neo, calls))
    with pytest.raises(Denied) as denial:
        f("synthetic-only", user="synthetic")
    assert denial.value.status_code == 409
    assert calls == []
    assert neo.closed


def test_job_launch_denied_without_side_effect():
    neo, calls = FakeNeo([False]), []
    f = function_from_source("jobs.py", "execute_task_job", make_env(neo, calls))
    outcome = f("synthetic-only")
    assert outcome["status"] == "DENIED"
    assert calls == []
    assert neo.closed


def test_job_terminal_uncertainty_does_not_report_done():
    neo, calls = FakeNeo([True, False]), []
    f = function_from_source("jobs.py", "execute_task_job", make_env(neo, calls))
    outcome = f("synthetic-only")
    assert outcome["status"] == "HOLD"
    assert calls == ["MODEL_OR_TASK_EXECUTION"]
    assert neo.closed


def test_cli_pipeline_does_not_dispatch_unclaimed_task():
    neo, calls, log = FakeNeo([False]), [], Log()
    f = function_from_source("pipeline_execute.py", "execute_ready",
                             make_env(neo, calls, log))
    f(neo)
    assert calls == []
    assert all(type(event) is tuple and event[0] != "info" for event in log.events)


def test_cli_pipeline_does_not_report_uncommitted_effect_as_success():
    neo, calls, log = FakeNeo([True, False]), [], Log()
    f = function_from_source("pipeline_execute.py", "execute_ready",
                             make_env(neo, calls, log))
    f(neo)
    assert calls == ["MODEL_OR_TASK_EXECUTION"]
    assert ("error", "Task terminal commit unverified; no success reported") in log.events
    assert not any(level == "info" for level, _ in log.events)


def test_api_legitimate_nonhermes_path_kept():
    neo, calls = FakeNeo([True, True]), []
    f = function_from_source("api.py", "execute_task", make_env(neo, calls))
    result = f("synthetic-only", user="synthetic")
    assert result["body"]["status"] == "DONE"
    assert result["status_code"] == 200
    assert calls == ["MODEL_OR_TASK_EXECUTION"]
