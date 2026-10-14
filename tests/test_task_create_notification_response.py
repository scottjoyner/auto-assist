"""Offline task API result contract without importing the live application.

Extract the exact api_create_task function AST from source and execute it
with a fake graph client. No FastAPI startup, graph, tokens or sockets.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src/assistx/api.py"


def _load_isolated_create_task(fake_graph):
    module = ast.parse(SOURCE.read_text())
    matches = [
        node for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "api_create_task"
    ]
    assert len(matches) == 1
    fn = matches[0]
    fn.decorator_list = []
    fn.returns = None
    for arg in fn.args.args:
        arg.annotation = None
    fn.args.defaults = [ast.Constant(value=None)]
    unit = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    bindings = {"_neo": lambda: fake_graph}
    exec(compile(unit, str(SOURCE), "exec"), bindings)
    return bindings["api_create_task"]


def _body():
    return SimpleNamespace(
        title="synthetic created task",
        task_type="task",
        status="READY",
        kind=None,
        required_capabilities=None,
        target_agent_id=None,
        priority=None,
        payload=None,
        correlation_id=None,
        idempotency_key=None,
        preemptible=False,
        max_migrations=2,
    )


@pytest.mark.parametrize("queued", [True, False])
def test_task_api_returns_notification_queue_disposition(queued):
    observed = []

    class FakeGraph:
        def create_task_with_context(self, **kwargs):
            observed.append(kwargs["title"])
            return {
                "task_id": "synthetic-api-task",
                "dispatch_id": None,
                "auto_assign_notification_queued": queued,
            }

        def close(self):
            observed.append("closed")

    create = _load_isolated_create_task(FakeGraph())
    result = create(_body(), user="synthetic")
    assert result["task_id"] == "synthetic-api-task"
    assert result["auto_assign_notification_queued"] is queued
    assert observed == ["synthetic created task", "closed"]


def test_task_api_missing_queuing_attestation_is_false():
    class FakeGraph:
        def create_task_with_context(self, **kwargs):
            return {"task_id": "synthetic-api-task", "dispatch_id": None}

        def close(self):
            pass

    create = _load_isolated_create_task(FakeGraph())
    result = create(_body(), user="synthetic")
    assert result["auto_assign_notification_queued"] is False
