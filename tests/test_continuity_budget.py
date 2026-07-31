from assistx.continuity_budget import memory_plan


def test_standby_plan_fits_with_small_headless_model():
    plan = memory_plan(mode="standby", headless_llm_mb=5_500)
    assert plan["required_fit"] is True
    assert plan["projected_fit"] is True
    assert plan["available_after_projected_mb"] >= plan["safety_reserve_mb"]


def test_durable_plan_requires_model_drain():
    plan = memory_plan(mode="durable", headless_llm_mb=5_500)
    assert plan["required_fit"] is True
    assert any("drain or stop" in action for action in plan["required_actions"])


def test_oversized_executor_plan_fails_closed():
    plan = memory_plan(
        mode="executor",
        total_mb=8_000,
        headless_llm_mb=5_500,
    )
    assert plan["required_fit"] is False
    assert any("reduce service memory caps" in action for action in plan["required_actions"])
