from assistx.allocation_engine import build_allocation_plan


def test_allocation_balances_value_and_current_load():
    tasks = [{"id": "task-1", "title": "Urgent diagnosis", "status": "READY", "priority": "HIGH"}]
    nodes = [
        {"hostname": "fast-busy", "online": True, "loaded_models": ["m"], "max_concurrent": 1, "inflight_tasks": 1},
        {"hostname": "steady-free", "online": True, "loaded_models": ["m"], "max_concurrent": 2, "inflight_tasks": 0},
    ]
    values = {"entries": [
        {"node_id": "fast-busy", "model_id": "m", "quality_score": .9, "confidence": .9, "tokens_per_second": 50},
        {"node_id": "steady-free", "model_id": "m", "quality_score": .8, "confidence": .9, "tokens_per_second": 40},
    ]}

    plan = build_allocation_plan(tasks, nodes, values)

    assert plan["summary"] == {"ready_tasks": 1, "placeable": 1, "blocked": 0}
    assert plan["recommendations"][0]["recommended"]["node_id"] == "steady-free"
    assert plan["recommendations"][0]["executable"] is False


def test_allocation_explains_unplaceable_work():
    plan = build_allocation_plan(
        [{"id": "task", "status": "READY", "required_capabilities": ["gpu"]}],
        [{"hostname": "cpu", "online": True, "capabilities": ["cpu"]}],
        {},
    )

    assert plan["summary"]["blocked"] == 1
    assert plan["recommendations"][0]["blocked_reason"]
    assert plan["recommendations"][0]["rejected"] == [
        {
            "node_id": "cpu",
            "reason": "capability_mismatch",
            "missing_capabilities": ["gpu"],
        }
    ]


def test_allocation_explains_opportunity_cost_and_operator_controls():
    plan = build_allocation_plan(
        [{"id": "task", "status": "READY"}],
        [
            {
                "hostname": "best",
                "online": True,
                "loaded_models": ["m"],
                "max_concurrent": 1,
            },
            {
                "hostname": "second",
                "online": True,
                "loaded_models": ["m"],
                "max_concurrent": 1,
            },
            {
                "hostname": "maintenance",
                "online": True,
                "is_blocked": True,
                "control_mode": "maintenance",
            },
        ],
        {
            "entries": [
                {
                    "node_id": "best",
                    "model_id": "m",
                    "quality_score": 0.9,
                    "confidence": 0.9,
                    "tokens_per_second": 50,
                },
                {
                    "node_id": "second",
                    "model_id": "m",
                    "quality_score": 0.7,
                    "confidence": 0.9,
                    "tokens_per_second": 30,
                },
            ]
        },
    )

    recommendation = plan["recommendations"][0]
    assert recommendation["recommended"]["node_id"] == "best"
    assert recommendation["opportunity_cost"] > 0
    assert "leads by" in recommendation["decision_summary"]
    assert {"node_id": "maintenance", "reason": "operator_control"} in recommendation[
        "rejected"
    ]


def test_verified_code_reliability_influences_bounded_change_routing():
    task = {
        "id": "task",
        "status": "READY",
        "kind": "bounded_code_change",
        "payload": {"execution_contract": {"kind": "bounded_code_change"}},
    }
    nodes = [
        {
            "hostname": "node",
            "online": True,
            "loaded_models": ["unproven", "verified"],
        }
    ]
    values = {
        "entries": [
            {
                "node_id": "node",
                "model_id": model,
                "quality_score": 0.7,
                "confidence": 0.8,
                "tokens_per_second": 20,
            }
            for model in ("unproven", "verified")
        ]
    }
    profiles = [
        {
            "model_id": "verified",
            "task_family": "bounded_code_change",
            "attempts": 8,
            "verified_successes": 8,
        }
    ]

    plan = build_allocation_plan([task], nodes, values, profiles)

    recommended = plan["recommendations"][0]["recommended"]
    assert recommended["model_id"] == "verified"
    assert recommended["components"]["verified_code_reliability"] == 0.9
    assert recommended["components"]["verified_code_attempts"] == 8


def test_cache_locality_can_avoid_repeating_large_prefill():
    prefix_id = "prefix-" + "a" * 64
    fingerprint = "b" * 64
    task = {
        "id": "cached-task",
        "status": "READY",
        "payload": {
            "kv_cache": {
                "prefix_id": prefix_id,
                "compatibility_fingerprint": fingerprint,
                "privacy_scope": "project",
                "scope_id": "auto-assist",
            }
        },
    }
    nodes = [
        {"hostname": "cached", "online": True, "loaded_models": ["model"]},
        {"hostname": "uncached", "online": True, "loaded_models": ["model"]},
    ]
    values = {
        "entries": [
            {
                "node_id": node,
                "model_id": "model",
                "quality_score": 0.8,
                "confidence": 0.8,
                "tokens_per_second": 50,
            }
            for node in ("cached", "uncached")
        ]
    }
    caches = [
        {
            "cache_id": "cache-1",
            "prefix_id": prefix_id,
            "compatibility_fingerprint": fingerprint,
            "privacy_scope": "project",
            "scope_id": "auto-assist",
            "node_id": "cached",
            "model_id": "model",
            "status": "READY",
            "expires_at_ts": 9_999_999_999_999,
            "token_count": 20_000,
            "bytes": 100,
            "capabilities": {},
        }
    ]

    plan = build_allocation_plan(
        [task],
        nodes,
        values,
        cache_manifests=caches,
    )

    recommended = plan["recommendations"][0]["recommended"]
    assert recommended["node_id"] == "cached"
    assert recommended["components"]["cache_mode"] == "local"
    assert recommended["components"]["cache_locality_bonus"] == 0.18
    assert recommended["components"]["cache_seconds_saved"] == 400
