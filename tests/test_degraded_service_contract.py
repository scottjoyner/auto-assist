from pathlib import Path

import yaml


def test_degraded_service_contract_is_bounded_and_fail_closed():
    contract = yaml.safe_load(
        Path("deploy/reconciliation/degraded-service-contract.yaml").read_text()
    )
    budget = contract["memory_budget_mb"]
    service_total = sum(budget["services"].values())
    assert service_total <= 2560
    assert budget["emergency_headroom"] >= 1024
    assert contract["state_contracts"]["falkordb"]["authority"] == "operational_only"
    assert contract["state_contracts"]["neo4j"]["authority"] == "durable_final"
    assert contract["state_contracts"]["redis"]["eviction_policy"] == "noeviction"
    assert contract["promotion_order"] == [
        "degraded",
        "assistx-shadow",
        "assistx-executor",
        "hermes-synthetic",
        "hermes-executor",
    ]
    assert contract["fail_closed"]["neo4j_unavailable"] == "pending_durable_commit"
    assert "autonomous_ssh_deployment" in contract["forbidden_in_degraded_mode"]
