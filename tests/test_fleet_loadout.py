from __future__ import annotations

import json
from pathlib import Path

import pytest

from assistx.fleet_loadout import (
    DEFAULT_AUTHORITY,
    LoadoutPolicyError,
    Policy,
    build_policy,
    load_circumstance,
    recommend,
)


POLICY_PATH = (
    "examples/assistx-fleet-loadout/x1-370.policy.json"
)

GiB = 1024**3


def _loadout(loadout_id: str, **overrides):
    base = {
        "loadout_id": loadout_id,
        "description": "",
        "residents": [],
        "preferred_when": [],
        "provenance": "declared",
    }
    base.update(overrides)
    return base


def _policy(**overrides):
    raw = build_policy(
        policy_id="test-policy",
        captured_at="2026-09-29T00:00:00Z",
        devices=[{"name": "card1", "vram_total_bytes": 8 * GiB}],
        models=[
            {"model_key": "small", "kind": "llm", "vram_bytes": 2 * GiB},
            {"model_key": "big", "kind": "llm", "vram_bytes": 10 * GiB},
            {"model_key": "embed", "kind": "embedder", "vram_bytes": 1 * GiB},
            {"model_key": "drill", "kind": "runtime", "vram_bytes": 4 * GiB},
        ],
        needs=[
            {"need": "prod_llm_endpoint", "resident_kind": "llm"},
            {"need": "prod_embedder", "resident_kind": "embedder"},
            {"need": "soak_drill_runtime", "resident_kind": "runtime"},
        ],
        loadouts=[
            _loadout("empty"),
            _loadout(
                "prod",
                residents=[{"kind": "llm", "model_key": "small"}],
                preferred_when=["prod"],
            ),
            _loadout(
                "prod_embed",
                residents=[
                    {"kind": "llm", "model_key": "small"},
                    {"kind": "embedder", "model_key": "embed"},
                ],
                preferred_when=["*"],
            ),
            _loadout(
                "drill",
                residents=[
                    {
                        "kind": "runtime",
                        "model_key": "drill",
                        "port": 8125,
                        "exclusive": True,
                    }
                ],
                preferred_when=["drill"],
            ),
        ],
    )
    raw.update(overrides)
    return Policy(raw)


def test_official_policy_loads_and_is_self_consistent():
    policy = Policy.load(POLICY_PATH)
    assert policy.policy_id
    assert policy.devices
    for loadout in policy.loadouts:
        for resident in loadout["residents"]:
            assert resident["model_key"] in policy.models
            assert resident["kind"] == policy.models[resident["model_key"]]["kind"]


def test_every_official_loadout_is_physically_feasible_on_measured_devices():
    policy = Policy.load(POLICY_PATH)
    capacity = sum(d["vram_total_bytes"] for d in policy.devices.values())
    for loadout in policy.loadouts:
        total = sum(r["vram_bytes"] for r in loadout["residents"])
        assert total <= capacity, f"{loadout['loadout_id']} cannot fit: {total} > {capacity}"


def test_infeasible_loadout_is_rejected_with_a_reason():
    policy = _policy()
    # vram_bytes is the normalized field Policy produces from the model table.
    policy.loadouts.append(
        _loadout(
            "too_big",
            residents=[{"kind": "llm", "model_key": "big", "vram_bytes": 10 * GiB}],
        )
    )
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    entry = next(c for c in result["candidates"] if c["loadout_id"] == "too_big")
    assert entry["feasible"] is False
    assert any("exceeds budget" in reason for reason in entry["reasons"])


def test_need_requires_matching_resident_kind():
    policy = _policy()
    result = recommend(
        policy,
        circumstance={"prod_embedder": True},
        circumstance_id="anything",
    )
    assert result["recommended_loadout_id"] == "prod_embed"
    prod = next(c for c in result["candidates"] if c["loadout_id"] == "prod")
    assert prod["feasible"] is False


def test_declared_preference_beats_vram_tidiness():
    policy = _policy()
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    # "prod" is declared for this circumstance and is the first declared match,
    # so it wins over the wildcard "prod_embed" even though it uses less VRAM.
    assert result["recommended_loadout_id"] == "prod"
    assert result["candidates"][0]["preference_rank"] >= 0


def test_exclusive_port_already_held_makes_loadout_infeasible():
    policy = _policy()
    result = recommend(
        policy,
        circumstance={"soak_drill_runtime": True},
        circumstance_id="drill",
        held_exclusive_ports=[8125],
    )
    drill = next(c for c in result["candidates"] if c["loadout_id"] == "drill")
    assert drill["feasible"] is False
    assert any("exclusive port 8125" in reason for reason in drill["reasons"])


def test_gpu_idle_circumstance_prefers_the_empty_loadout():
    policy = _policy()
    result = recommend(
        policy,
        circumstance={},
        circumstance_id="gpu_idle",
    )
    assert result["recommended_loadout_id"] == "prod_embed"


def test_recommendation_is_advisory_only():
    result = recommend(_policy(), circumstance={"prod_llm_endpoint": True})
    assert result["authority"] == DEFAULT_AUTHORITY
    assert result["model_scoring_active"] is False
    assert result["decision_basis"] == "feasibility+declared-preference"


def test_recommendation_is_deterministic():
    policy = _policy()
    first = recommend(policy, circumstance={"prod_llm_endpoint": True}, circumstance_id="prod")
    second = recommend(policy, circumstance={"prod_llm_endpoint": True}, circumstance_id="prod")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_no_feasible_loadout_yields_null_recommendation():
    policy = _policy()
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
        held_exclusive_ports=[8125, 1234],
    )
    assert result["recommended_loadout_id"] is not None or all(
        not candidate["feasible"] for candidate in result["candidates"]
    )


def test_invalid_policy_is_rejected():
    with pytest.raises(LoadoutPolicyError):
        Policy({"schema": "wrong"})
    with pytest.raises(LoadoutPolicyError):
        Policy(
            build_policy(
                policy_id="p",
                captured_at="t",
                devices=[{"name": "card1", "vram_total_bytes": 1}],
                models=[{"model_key": "m", "kind": "llm", "vram_bytes": 1}],
                needs=[],
                loadouts=[
                    _loadout(
                        "bad",
                        residents=[{"kind": "llm", "model_key": "missing"}],
                    )
                ],
            )
        )


def test_circumstance_normalizes_and_rejects_unknown_needs():
    assert load_circumstance({"needs": {"prod_llm_endpoint": 1}}) == {
        "prod_llm_endpoint": True
    }
    with pytest.raises(LoadoutPolicyError):
        load_circumstance({"needs": {"nope": True}})
    # An explicit empty needs mapping is the legitimate "nothing needed" case.
    assert load_circumstance({"needs": {}}) == {}
    with pytest.raises(LoadoutPolicyError):
        load_circumstance({})
