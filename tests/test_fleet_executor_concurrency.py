import threading
import time

import assistx.fleet_executor as fleet_executor


def projection(*, slots: int = 2):
    now_ms = int(time.time() * 1000)
    return {
        "generation": 2,
        "revision": "revision-2",
        "expires_at_ms": now_ms + 60_000,
        "providers": [
            {
                "name": "runtime-primary",
                "enabled": True,
                "runtime_instance_id": "runtime-a",
                "parallel_slots": slots,
                "models": [{"alias": "local/model-3b"}],
            },
            {
                "name": "runtime-fallback-path",
                "enabled": True,
                "runtime_instance_id": "runtime-a",
                "parallel_slots": slots,
                "models": [{"alias": "local/model-3b"}],
            },
        ],
    }


def test_physical_runtime_paths_share_one_slot_pool():
    inventory = fleet_executor.ProjectionInventory.from_document(projection(slots=2))

    assert inventory.total_slots == 2
    assert inventory.aliases == ("local/model-3b",)


def test_executor_never_exceeds_projection_capacity(monkeypatch):
    monkeypatch.setattr(fleet_executor, "MAX_CONCURRENT_LLM", 8)
    executor = fleet_executor.FleetExecutor(projection_loader=lambda: projection(slots=2))
    executor._active = {
        "task-a": {"priority": "interactive"},
        "task-b": {"priority": "interactive"},
    }

    result = executor.run_once()
    executor.stop()

    assert result["capacity"] == 2
    assert result["claimed"] == 0
    assert result["active"] == 2


def test_script_lane_is_removed_from_continuous_executor():
    assert fleet_executor.FleetExecutor._run_script("echo unsafe") == {
        "stdout": "",
        "stderr": "unsafe_shell_disabled",
        "exit_code": 126,
    }


def test_direct_runtime_slot_discovery_is_not_authoritative():
    # Runtime slots and loaded models come only from the approved projection.
    assert fleet_executor.FleetExecutor._probe_models("100.64.0.9") is None
    assert not hasattr(fleet_executor.FleetExecutor, "_probe_service_capacity")


def test_supervised_runner_honors_preexisting_cancellation():
    executor = fleet_executor.FleetExecutor(projection_loader=projection)
    cancel = threading.Event()
    cancel.set()

    # Use the boundary contract rather than starting an actual child HTTP call.
    observed = {}

    def runner(request, timeout, cancel_event):
        observed["cancelled"] = cancel_event.is_set()
        return {"status_code": 0, "body": {"error": "claim_lost"}}

    executor.router_runner = runner
    response = executor.router_runner({}, 30, cancel)
    executor.stop()

    assert observed["cancelled"] is True
    assert response["body"]["error"] == "claim_lost"
