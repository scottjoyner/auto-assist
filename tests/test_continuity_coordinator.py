from assistx.continuity_coordinator import (
    ContinuityCoordinator,
    plan_role_assignments,
    select_task_node,
)
from assistx.continuity_state import ContinuityConfig, InMemoryContinuityStore


def test_role_plan_avoids_headless_llm_for_durable_committer():
    services = [
        {
            "node_id": "beelink",
            "status": "healthy",
            "capabilities": [
                "continuity_state",
                "continuity_scheduler",
                "router",
                "recovery_island",
                "neo4j_commit",
                "headless_llm_active",
            ],
            "memory_available_mb": 5200,
            "active_slots": 0,
            "max_slots": 1,
        },
        {
            "node_id": "xwing",
            "status": "healthy",
            "capabilities": ["neo4j_commit", "backup_verify"],
            "memory_available_mb": 8000,
            "active_slots": 0,
            "max_slots": 2,
        },
    ]
    plans = {item["role"]: item for item in plan_role_assignments(services)}
    assert plans["continuity_leader"]["selected"]["node_id"] == "beelink"
    assert plans["durable_committer"]["selected"]["node_id"] == "xwing"


def test_task_selection_uses_capability_and_headroom():
    selected = select_task_node(
        {"required_capabilities": ["code"], "preferred_nodes": ["xwing"]},
        [
            {
                "node_id": "beelink",
                "status": "healthy",
                "capabilities": ["code"],
                "memory_available_mb": 1000,
                "active_slots": 0,
                "max_slots": 1,
            },
            {
                "node_id": "xwing",
                "status": "healthy",
                "capabilities": ["code"],
                "memory_available_mb": 6000,
                "active_slots": 0,
                "max_slots": 2,
            },
        ],
    )
    assert selected and selected["node_id"] == "xwing"


def test_coordinator_acquires_only_roles_selected_for_local_node():
    store = InMemoryContinuityStore(
        ContinuityConfig("fleet", "beelink", "continuity-secret-123456")
    )
    store.advance_epoch(1, "witness:epoch-1")
    store.record_heartbeat(
        {
            "node_id": "beelink",
            "status": "healthy",
            "capabilities": [
                "continuity_state",
                "continuity_scheduler",
                "router",
                "recovery_island",
            ],
            "memory_available_mb": 4000,
            "active_slots": 0,
            "max_slots": 1,
        }
    )
    outcomes = ContinuityCoordinator(store, node_id="beelink").acquire_selected_roles(
        fence_proof="witness:epoch-1"
    )
    assert outcomes
    assert all(item["ok"] for item in outcomes)
    assert {item["role"] for item in outcomes} >= {
        "continuity_leader",
        "scheduler_lite",
        "router_authority",
    }
