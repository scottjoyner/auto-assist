from __future__ import annotations

import json

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


def test_collect_runtimes_drops_expired_loaded_models(monkeypatch) -> None:
    now = control_room._now_ms()
    monkeypatch.setattr(
        control_room,
        "_runtime_inventory",
        lambda _factory: [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "node_id": "xwing",
                "runtime_kind": "lmstudio",
                "status": "online",
                "loaded_models": [
                    {"model_key": "fresh/model", "expires_at_ts": now + 60_000},
                    {"model_key": "expired/model", "expires_at_ts": now - 60_000},
                    {"model_key": "expired/older", "expires_at_ts": now - 10**9},
                ],
            }
        ],
    )
    monkeypatch.setattr(control_room, "_endpoint_inventory", lambda _factory: [])

    runtimes = control_room.collect_runtimes(lambda: None, {})

    assert len(runtimes) == 1
    runtime = runtimes[0]
    assert [m["model_key"] for m in runtime["loaded_models"]] == ["fresh/model"]
    assert runtime["stale_loaded_models"] == 2


def test_collect_runtimes_endpoint_library_scan_is_not_loaded_models(monkeypatch) -> None:
    # models_json holds the node's downloaded library (on-disk paths from a
    # scan) — it is catalog data and must never render as loaded models.
    monkeypatch.setattr(control_room, "_runtime_inventory", lambda _factory: [])
    monkeypatch.setattr(
        control_room,
        "_endpoint_inventory",
        lambda _factory: [
            {
                "runtime_instance_id": "lmstudio-deathstar-1234",
                "node_id": "deathstar-xps-8920",
                "base_url": "http://100.78.106.121:1234/v1",
                "models_json": json.dumps([
                    {"model_id": "/mnt/8TB/apps/lmstudio/models/VibeThinker-3B-Hermes-v04-Q8_0.gguf"}
                ]),
            }
        ],
    )

    runtimes = control_room.collect_runtimes(lambda: None, {})

    assert len(runtimes) == 1
    runtime = runtimes[0]
    assert runtime["loaded_models"] == []
    assert runtime["catalog_models"][0]["model_key"].endswith(".gguf")
