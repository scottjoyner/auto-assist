from __future__ import annotations

from assistx.benchmark_allocation_policy import _enrich_nodes


def _policy() -> dict:
    return {
        "optiplex": {
            "node_id": "optiplex",
            "worker_mode": "auxiliary",
            "roles": ["auxiliary_llm", "compression"],
            "allow_agent_runtime": False,
            "allow_code_execution": False,
        }
    }


def test_failed_exact_loadout_is_removed_but_unmeasured_fallback_remains() -> None:
    nodes = [
        {
            "hostname": "optiplex",
            "online": True,
            "capabilities": ["llm"],
            "loaded_models": ["failed-fast", "unmeasured-small"],
        }
    ]
    profiles = {
        ("optiplex", "failed-fast", "compression"): {
            "quality_floor_passed": False,
            "quality_score": 0.2,
            "tokens_per_second": 100.0,
        }
    }

    enriched = _enrich_nodes(
        nodes,
        _policy(),
        "compression",
        profiles,
    )

    assert enriched[0]["loaded_models"] == ["unmeasured-small"]
    assert enriched[0]["quality_floor_rejected_models"] == ["failed-fast"]
    assert enriched[0].get("is_blocked") is not True


def test_node_is_blocked_when_every_loaded_model_fails_floor() -> None:
    nodes = [
        {
            "hostname": "optiplex",
            "online": True,
            "capabilities": ["llm"],
            "loaded_models": ["failed-fast"],
        }
    ]
    profiles = {
        ("optiplex", "failed-fast", "compression"): {
            "quality_floor_passed": False,
        }
    }

    enriched = _enrich_nodes(
        nodes,
        _policy(),
        "compression",
        profiles,
    )

    assert enriched[0]["loaded_models"] == []
    assert enriched[0]["is_blocked"] is True
    assert enriched[0]["control_mode"] == "benchmark_quality_floor_failed"
