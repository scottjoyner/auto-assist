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
    def __init__(self, tasks: list[dict[str, Any]], *, omit_claim_id: bool = False,
                 capacity: int = 2):
        self.tasks = tasks
        self.omit_claim_id = omit_claim_id
        self.capacity = capacity
        self.projection_generation = 7
        self.supply_generation = 7
        self.owner_receipt = "a" * 64  # synthetic grant digest, no real signing key
        self.expires_at_ts = int(time.time() * 1000) + 120_000
        self.generated_at_ts = int(time.time() * 1000)
        self.claimed: list[str] = []
        self.completed: list[dict[str, Any]] = []
        self.heartbeats: list[dict[str, Any]] = []
        self.lock = threading.Lock()


class FakeSession:
    def __init__(self, state: SharedState):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def run(self, cypher, *_args, **_kwargs):
        assert "OPTIONAL MATCH (w:WorkSupplyState {name:'canonical'})" in cypher
        return self

    def single(self, *, strict=False):
        assert strict is True
        return {
            "generation": self.state.projection_generation,
            "status": "approved",
            "expires_at_ts": self.state.expires_at_ts,
            "owner_receipt": self.state.owner_receipt,
            "supply_generated_at_ts": self.state.generated_at_ts,
            "supply_generation": self.state.supply_generation,
            "supply_slots": self.state.capacity,
        }


class FakeNeo:
    def __init__(self, state: SharedState):
        self.state = state

    def _session(self):
        return FakeSession(self.state)

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


def test_background_backfills_nine_of_ten_approved_slots(monkeypatch) -> None:
    """Admitted idle capacity should be used without taking the foreground reserve."""
    monkeypatch.setattr(module, "MAX_CONCURRENT_LLM", 10)
    monkeypatch.setattr(module, "BACKGROUND_MAX_FRACTION", 0.9)
    tasks = [
        {
            "id": f"bounded-background-{i}",
            "priority": "background",
            "required_capabilities": ["llm"],
            "payload": {"prompt": f"Analyze bounded sample {i}"},
        }
        for i in range(10)
    ]
    state = SharedState(tasks, capacity=10)

    def runner(
        request: dict[str, Any], timeout: int, cancel: threading.Event
    ) -> dict[str, Any]:
        return {
            "status_code": 200,
            "body": {"choices": [{"message": {"content": "Analysis completed"}}]},
        }

    executor = module.FleetExecutor(
        neo_factory=FakeFactory(state),
        projection_loader=lambda: projection(slots=10),
        router_runner=runner,
    )
    executor._list_ready_llm_tasks = lambda neo, limit: list(tasks)  # type: ignore[method-assign]
    result = executor.run_once()
    wait_until_idle(executor)
    executor.stop()

    assert result["capacity"] == 10
    assert result["background_limit"] == 9
    assert result["claimed"] == 9
    assert len(state.claimed) == 9


def test_background_never_bypasses_expired_projection(monkeypatch) -> None:
    monkeypatch.setattr(module, "BACKGROUND_MAX_FRACTION", 0.9)
    executor = module.FleetExecutor(
        projection_loader=lambda: {"generation": 896, "expires_at_ms": 0}
    )
    result = executor.run_once()
    executor.stop()
    assert result["executed"] is False
    assert result["reason"] == "projection_unavailable"


def test_expired_or_mismatched_supply_denies_before_any_task_claim():
    tasks = [{"id": "pending","priority":"interactive","required_capabilities":["llm"],
              "payload":{"prompt":"Never dispatch on stale capacity"}}]
    state = SharedState(tasks)
    state.generated_at_ts = int(time.time() * 1000) - 3_600_000
    touched = []
    executor = module.FleetExecutor(
        neo_factory=FakeFactory(state),projection_loader=projection,
        router_runner=lambda *_:touched.append('effect'),
    )
    executor._list_ready_llm_tasks = lambda *_: touched.append('listed')  # type: ignore[method-assign]
    result = executor.run_once()
    executor.stop()
    assert result["executed"] is False
    assert result["reason"] == "work_supply_or_owner_grant_unavailable"
    assert result["gate"] == "work_supply_heartbeat_expired"
    assert state.claimed == [] and touched == []


def test_lost_owner_approval_after_task_claim_prevents_model_effect():
    tasks = [{"id": "revoked","priority":"interactive","required_capabilities":["llm"],
              "payload":{"prompt":"Should not run"}}]
    state = SharedState(tasks)
    runner_calls = []
    class RevokingNeo(FakeNeo):
        def claim_task(self,*args,**kwargs):
            result = super().claim_task(*args,**kwargs)
            self.state.owner_receipt = None
            return result
    executor = module.FleetExecutor(
        neo_factory=lambda: RevokingNeo(state),projection_loader=projection,
        router_runner=lambda *_:runner_calls.append('unsafe_model_effect'),
    )
    executor._list_ready_llm_tasks = lambda *_: tasks  # type: ignore[method-assign]
    result = executor.run_once()
    wait_until_idle(executor)
    executor.stop()
    assert result["claimed"] == 1
    assert state.claimed == ["revoked"]
    assert runner_calls == []
    assert state.completed == []


def test_generation_change_during_model_call_blocks_task_commit():
    tasks = [{"id": "generation-shift","priority":"interactive",
              "required_capabilities":["llm"],"payload":{"prompt":"Do work"}}]
    state = SharedState(tasks)
    called = []
    def runner(request,timeout,cancel):
        called.append(request['model'])
        state.supply_generation = 8
        return {"status_code":200,"body":{"choices":[{"message":{"content":"complete but revoked"}}]}}
    executor = module.FleetExecutor(
        neo_factory=FakeFactory(state),projection_loader=projection,router_runner=runner,
    )
    executor._list_ready_llm_tasks = lambda *_: tasks  # type: ignore[method-assign]
    result = executor.run_once()
    wait_until_idle(executor)
    executor.stop()
    assert result['claimed'] == 1
    assert called
    assert state.completed == []


def test_generation_change_before_claim_denies_task_graph_mutation():
    tasks = [{"id":"cancelled","priority":"interactive","required_capabilities":["llm"],
              "payload":{"prompt":"No claim"}}]
    state = SharedState(tasks)
    executor = module.FleetExecutor(
        neo_factory=FakeFactory(state),projection_loader=projection,
        router_runner=lambda *_:None,
    )
    def revoke_on_list(*_args):
        state.projection_generation = 8
        return tasks
    executor._list_ready_llm_tasks = revoke_on_list  # type: ignore[method-assign]
    result = executor.run_once()
    executor.stop()
    assert result['claimed'] == 0
    assert state.claimed == []


def test_inflight_controller_expiry_cancels_long_running_model(monkeypatch):
    monkeypatch.setattr(module, "HEARTBEAT_INTERVAL", 0.02)
    tasks = [{"id":"inflight-revoke","priority":"interactive",
              "required_capabilities":["llm"],"payload":{"prompt":"Only while authorized"}}]
    state = SharedState(tasks)
    started = threading.Event()
    observed_cancel = threading.Event()
    def runner(_request, _timeout, cancel):
        started.set()
        deadline=time.time()+1.5
        while time.time()<deadline and not cancel.is_set():
            time.sleep(0.005)
        if cancel.is_set():
            observed_cancel.set()
        return {"status_code":200,"body":{"choices":[{"message":{"content":"not authoritative"}}]}}
    executor=module.FleetExecutor(
        neo_factory=FakeFactory(state),projection_loader=projection,router_runner=runner,
    )
    executor._list_ready_llm_tasks=lambda *_: tasks  # type: ignore[method-assign]
    result=executor.run_once()
    assert started.wait(1.0)
    state.owner_receipt=None
    wait_until_idle(executor)
    executor.stop()
    assert result['claimed']==1
    assert observed_cancel.is_set()
    assert state.completed==[]
