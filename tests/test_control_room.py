from __future__ import annotations

from assistx import control_room


def test_classify_runtime_mode() -> None:
    assert control_room.classify_runtime_mode("LM Studio 0.4") == "LM_STUDIO"
    assert control_room.classify_runtime_mode("llama.cpp server") == "HEADLESS"
    assert control_room.classify_runtime_mode("custom", True) == "HEADLESS"
    assert control_room.classify_runtime_mode("custom", False) == "LM_STUDIO"
    assert control_room.classify_runtime_mode(None) == "UNKNOWN"


def test_human_activity_title_prefers_task_context() -> None:
    assert control_room.human_activity_title("Fix router timeout", "diagnosis", "abc") == "Fix router timeout"
    assert control_room.human_activity_title(None, "repo_analysis", "abc") == "Repo analysis"
    assert control_room.human_activity_title(None, None, "1234567890") == "Task 12345678"


def test_collect_runtimes_keeps_paths_under_one_slot_pool(monkeypatch) -> None:
    monkeypatch.setattr(
        control_room,
        "_runtime_inventory",
        lambda _factory: [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "node_id": "xwing",
                "runtime_kind": "lmstudio",
                "runtime_version": "0.4.7",
                "headless": False,
                "loaded_models": [{"model_key": "qwen/test", "quantization": "Q4"}],
            }
        ],
    )
    monkeypatch.setattr(
        control_room,
        "_endpoint_inventory",
        lambda _factory: [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "node_id": "xwing",
                "base_url": "http://192.168.1.9:1234/v1",
            }
        ],
    )
    admission = {
        "runtimes": [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "parallel_slots": 1,
                "active": 1,
                "queued": 2,
                "queue_limit": 4,
            }
        ],
        "access_paths": [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "approved_access_urls": [
                    "http://192.168.1.9:1234/v1",
                    "http://100.64.0.9:1234/v1",
                ],
                "selected_access_url": "http://192.168.1.9:1234/v1",
                "selected_transport": "lan",
                "selection_fresh": True,
            }
        ],
    }

    runtimes = control_room.collect_runtimes(lambda: None, admission)

    assert len(runtimes) == 1
    runtime = runtimes[0]
    assert runtime["runtime_instance_id"] == "lmstudio-xwing-1234"
    assert runtime["runtime_mode"] == "LM_STUDIO"
    assert runtime["parallel_slots"] == 1
    assert runtime["active"] == 1
    assert runtime["queued"] == 2
    assert runtime["selected_transport"] == "lan"
    assert len(runtime["access_paths"]) == 2


def test_build_overview_fails_closed_on_required_dependency(monkeypatch) -> None:
    monkeypatch.setattr(
        control_room,
        "collect_dependencies",
        lambda _factory: (
            [
                {
                    "name": "Neo4j",
                    "required": True,
                    "status": "unhealthy",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(control_room, "collect_runtimes", lambda _factory, _admission: [])
    monkeypatch.setattr(control_room, "collect_activity", lambda _factory: [])
    monkeypatch.setattr(control_room, "collect_performance", lambda _factory: [])

    snapshot = control_room.build_overview(lambda: None)

    assert snapshot["overall_status"] == "degraded"
    assert snapshot["summary"]["required_dependency_failures"] == 1


def test_legacy_operator_pages_are_consolidated() -> None:
    # Only true duplicates stay consolidated; operator pages render again.
    assert "/" in control_room.LEGACY_UI_PATHS
    assert "/command-center" in control_room.LEGACY_UI_PATHS
    assert "/fleet" in control_room.LEGACY_UI_PATHS
    for revived in ("/live", "/operations", "/fleet-dashboard", "/strategy", "/routing"):
        assert revived not in control_room.LEGACY_UI_PATHS
