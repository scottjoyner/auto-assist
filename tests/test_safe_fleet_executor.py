from __future__ import annotations

import threading
import time
from typing import Any

from assistx import fleet_executor as module


def projection(*, slots: int = 2) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    return {
        "generation": 7,
        "revision": "rev-7",
        "generated_at_ms": now_ms,
        "expires_at_ms": now_ms + 60_000,
        "providers": [
            {
                "name": "node-a-primary",
                "enabled": True,
                "runtime_instance_id": "runtime-a",
                "parallel_slots": slots,
                "models": [
                    {"alias": "local/small-3b"},
                    {"alias": "local/quality-27b"},
                ],
            },
            {
                "name": "node-a-fallback-path",
                "enabled": True,
                "runtime_instance_id": "runtime-a",
                "parallel_slots": slots,
                "models": [{"alias": "local/small-3b"}],
            },
        ],
    }


class SharedState:
    def __init__(self, tasks: list[dict[str, Any]], *, omit_claim_id: bool = False):
        self.tasks = tasks
        self.omit_claim_id = omit_claim_id
        self.claimed: list[str] = []
        self.completed: list[dict[str, Any]] = []
        self.heartbeats: list[dict[str, Any]] = []
        self.lock = threading.Lock()


class FakeNeo:
    def __init__(self, state: SharedState):
        self.state = state

    def claim_task(self, task_id: str, agent_id: str, **kwargs: Any) -> dict[str, Any]:
        with self.state.lock:
            task = next(row for row in self.state.tasks if row["id"] == task_id)
            self.state.claimed.append(task_id)
        claimed = dict(task)
        claimed["status"] = "CLAIMED"
        if not self.state.omit_claim_id:
            claimed["claim_id"] = f"claim-{task_id}"
        return {"claimed": True, "task": claimed}

    def heartbeat_task(self, task_id: str, agent_id: str, **kwargs: Any) -> dict[str, Any]:
        self.state.heartbeats.append(
            {"task_id": task_id, "agent_id": agent_id, **kwargs}
        )
        return {"id": task_id, "claim_id": kwargs.get("claim_id")}

    def complete_task(
        self,
        task_id: str,
        agent_id: str,
        status: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.state.completed.append(
            {
                "task_id": task_id,
                "agent_id": agent_id,
                "status": status,
                **kwargs,
            }
        )
        return {"id": task_id, "status": status}

    def close(self) -> None:
        return None


class FakeFactory:
    def __init__(self, state: SharedState):
        self.state = state

    def __call__(self) -> FakeNeo:
        return FakeNeo(self.state)


def wait_until_idle(executor: module.FleetExecutor) -> None:
    deadline = time.time() + 3
    while executor.status()["active_count"] and time.time() < deadline:
        time.sleep(0.01)
    assert executor.status()["active_count"] == 0


def test_projection_deduplicates_physical_runtime_capacity() -> None:
    inventory = module.ProjectionInventory.from_document(projection(slots=3))

    assert inventory.total_slots == 3
    assert set(inventory.aliases) == {"local/small-3b", "local/quality-27b"}
    assert inventory.choose_model("", "high") == "local/quality-27b"
    assert inventory.choose_model("", "low") == "local/small-3b"


def test_executor_routes_claim_lineage_through_router() -> None:
    tasks = [
        {
            "id": "task-1",
            "priority": "interactive",
            "required_capabilities": ["llm"],
            "payload": {
                "prompt": "Inspect the current fleet state.",
                "model": "local/quality-27b",
            },
        }
    ]
    state = SharedState(tasks)
    observed: dict[str, Any] = {}

    def runner(
        request: dict[str, Any], timeout: int, cancel: threading.Event
    ) -> dict[str, Any]:
        observed.update(request)
        return {
            "status_code": 200,
            "body": {
                "model": request["model"],
                "choices": [{"message": {"content": "Completed fleet analysis."}}],
                "usage": {"completion_tokens": 4},
            },
        }

    executor = module.FleetExecutor(
        neo_factory=FakeFactory(state),
        projection_loader=projection,
        router_runner=runner,
    )
    executor._list_ready_llm_tasks = lambda neo, limit: list(tasks)  # type: ignore[method-assign]

    result = executor.run_once()
    wait_until_idle(executor)
    executor.stop()

    assert result["claimed"] == 1
    assert state.claimed == ["task-1"]
    lineage = observed["metadata"]["assistx_executor"]
    assert lineage == {
        "task_id": "task-1",
        "claim_id": "claim-task-1",
        "agent_id": module.EXECUTOR_AGENT_ID,
        "projection_generation": 7,
    }
    assert state.completed[0]["claim_id"] == "claim-task-1"
    assert state.completed[0]["status"] == "DONE"


def test_background_work_yields_when_interactive_work_waits(monkeypatch) -> None:
    monkeypatch.setattr(module, "MAX_CONCURRENT_LLM", 2)
    tasks = [
        {
            "id": "interactive",
            "priority": "interactive",
            "required_capabilities": ["llm"],
            "payload": {"prompt": "urgent"},
        },
        {
            "id": "background",
            "priority": "background",
            "required_capabilities": ["llm"],
            "payload": {"prompt": "research"},
        },
    ]
    state = SharedState(tasks)

    def runner(
        request: dict[str, Any], timeout: int, cancel: threading.Event
    ) -> dict[str, Any]:
        return {
            "status_code": 200,
            "body": {"choices": [{"message": {"content": "finished work"}}]},
        }

    executor = module.FleetExecutor(
        neo_factory=FakeFactory(state),
        projection_loader=projection,
        router_runner=runner,
    )
    executor._list_ready_llm_tasks = lambda neo, limit: list(tasks)  # type: ignore[method-assign]

    result = executor.run_once()
    wait_until_idle(executor)
    executor.stop()

    assert result["claimed"] == 1
    assert state.claimed == ["interactive"]


def test_claim_without_claim_id_never_executes() -> None:
    tasks = [
        {
            "id": "unsafe-claim",
            "priority": "interactive",
            "required_capabilities": ["llm"],
            "payload": {"prompt": "do not run"},
        }
    ]
    state = SharedState(tasks, omit_claim_id=True)
    called = False

    def runner(
        request: dict[str, Any], timeout: int, cancel: threading.Event
    ) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"status_code": 200, "body": {}}

    executor = module.FleetExecutor(
        neo_factory=FakeFactory(state),
        projection_loader=projection,
        router_runner=runner,
    )
    executor._list_ready_llm_tasks = lambda neo, limit: list(tasks)  # type: ignore[method-assign]

    result = executor.run_once()
    executor.stop()

    assert result["claimed"] == 0
    assert called is False
    assert state.completed == []


def test_script_lane_is_disabled() -> None:
    assert module.FleetExecutor._run_script("touch /tmp/should-not-exist") == {
        "stdout": "",
        "stderr": "unsafe_shell_disabled",
        "exit_code": 126,
    }
    assert module.FleetExecutor._probe_models("127.0.0.1") is None
