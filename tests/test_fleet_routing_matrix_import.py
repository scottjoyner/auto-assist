from __future__ import annotations

from assistx import benchmark_allocation_policy
from assistx import runtime_projection_v2
from assistx.fleet_context_projection import merge_nodes
from assistx.fleet_routing_matrix import matrix_fingerprint, validate_matrix


def _matrix() -> dict:
    return {
        "schema_version": "fleet_routing_matrix.v1",
        "generated_at_utc": "2026-08-06T14:00:00Z",
        "summary": {"tailnet_nodes": 3},
        "policy": {"discovery_is_not_admission": True},
        "nodes": [
            {
                "node_id": "x1-370",
                "online": True,
                "roles": ["full_agent", "code_agent"],
                "capabilities": ["llm", "coding"],
                "worker_mode": "agent",
                "allow_agent_runtime": True,
                "allow_code_execution": True,
                "tailscale_ips": ["100.64.0.10"],
            },
            {
                "node_id": "optiplex",
                "online": True,
                "roles": ["auxiliary_llm", "summarization"],
                "capabilities": ["llm", "summarization"],
                "worker_mode": "auxiliary",
                "allow_agent_runtime": False,
                "allow_code_execution": False,
                "tailscale_ips": ["100.64.0.11"],
            },
            {
                "node_id": "iphone",
                "online": True,
                "roles": ["observer"],
                "capabilities": ["inventory"],
                "worker_mode": "observer_only",
                "allow_agent_runtime": False,
                "allow_code_execution": False,
                "tailscale_ips": ["100.64.0.12"],
            },
        ],
        "profiles": [
            {
                "node_id": "optiplex",
                "model_id": "small-summary",
                "task_family": "summarization",
                "quality_score": 0.75,
                "quality_confidence": 1.0,
                "reliability": 0.95,
                "tokens_per_second": 30.0,
                "speed_score": 0.6,
                "utility_score": 0.82,
                "quality_floor": 0.52,
                "quality_floor_passed": True,
                "roles": ["auxiliary_llm", "summarization"],
                "worker_mode": "auxiliary",
            }
        ],
        "rankings": {"summarization": []},
        "admission": {"admitted": False},
    }


def test_matrix_keeps_all_tailnet_nodes_and_is_deterministic() -> None:
    first = validate_matrix(_matrix())
    second = validate_matrix(_matrix())

    assert len(first["nodes"]) == 3
    assert next(row for row in first["nodes"] if row["node_id"] == "iphone")[
        "worker_mode"
    ] == "observer_only"
    assert matrix_fingerprint(first) == matrix_fingerprint(second)


def test_context_merge_keeps_observer_visible_and_blocked() -> None:
    discovered = [
        {
            "node_id": "iphone",
            "display_name": "iPhone",
            "lane": "blocked",
            "running": True,
            "capabilities": ["observer", "inventory"],
            "services": [],
        },
        {
            "node_id": "optiplex",
            "display_name": "OptiPlex",
            "lane": "local",
            "running": True,
            "capabilities": ["auxiliary_llm", "summarization"],
            "services": [],
        },
    ]

    result = {row["node_id"]: row for row in merge_nodes([], discovered)}

    assert result["iphone"]["lane"] == "blocked"
    assert result["iphone"]["running"] is True
    assert result["optiplex"]["lane"] == "local"


def test_runtime_projection_enriches_only_existing_admitted_model(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_projection_v2,
        "benchmark_projection_index",
        lambda _factory: {
            ("optiplex", "small-summary"): {
                "routing_roles": ["auxiliary_llm", "summarization"],
                "worker_mode": "auxiliary",
                "allow_agent_runtime": False,
                "allow_code_execution": False,
                "task_family_scores": {
                    "summarization": {
                        "quality_score": 0.75,
                        "quality_floor_passed": True,
                        "utility_score": 0.82,
                    }
                },
            }
        },
    )
    document = {
        "providers": [
            {
                "node_id": "optiplex",
                "models": [
                    {
                        "alias": "small-summary",
                        "provider_model": "small-summary",
                    }
                ],
            }
        ]
    }

    runtime_projection_v2._apply_benchmark_routing(document, lambda: None)

    model = document["providers"][0]["models"][0]
    assert model["worker_mode"] == "auxiliary"
    assert model["task_family_scores"]["summarization"]["utility_score"] == 0.82
    assert len(document["providers"]) == 1
    assert len(document["providers"][0]["models"]) == 1


def test_allocation_policy_blocks_auxiliary_coding_but_allows_summary() -> None:
    policies = {
        "optiplex": {
            "node_id": "optiplex",
            "worker_mode": "auxiliary",
            "roles": ["auxiliary_llm", "summarization"],
            "allow_code_execution": False,
        }
    }
    nodes = [
        {
            "hostname": "optiplex",
            "online": True,
            "capabilities": ["llm"],
            "loaded_models": ["small-summary"],
        }
    ]

    summary = benchmark_allocation_policy._enrich_nodes(nodes, policies, "summarization")
    coding = benchmark_allocation_policy._enrich_nodes(nodes, policies, "coding")

    assert summary[0].get("is_blocked") is not True
    assert coding[0]["is_blocked"] is True
