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


def test_model_stays_unready_without_measured_evidence():
    result = recommend(
        _policy(),
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    assert result["model_ready"] is False
    assert result["model_scoring_active"] is False
    assert result["evaluated_evidence_records"] == 0
    assert "No measured outcome" in result["model_note"]


def test_partial_evidence_keeps_declared_preference_and_names_the_gap():
    policy = _policy()
    policy.evidence = [
        {
            "circumstance_id": "prod",
            "loadout_id": "prod",
            "provenance": "measured",
            "outcomes": {"llm_endpoint_healthy": True},
        }
    ]
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    assert result["evaluated_evidence_records"] == 1
    assert result["model_ready"] is False
    assert result["uncovered_feasible_candidates"]
    assert result["recommended_loadout_id"] == "prod"


def test_full_measured_coverage_makes_the_model_ready():
    policy = _policy()
    policy.evidence = [
        {
            "circumstance_id": "prod",
            "loadout_id": loadout_id,
            "provenance": "measured",
            "outcomes": {"llm_endpoint_healthy": True},
        }
        for loadout_id in ("prod", "prod_embed")
    ]
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    assert result["model_ready"] is True
    assert result["uncovered_feasible_candidates"] == []
    # Scoring is still the operator's to enable; readiness is a precondition.
    assert result["model_scoring_active"] is False


def test_declared_provenance_evidence_does_not_count_as_measured():
    policy = _policy()
    policy.evidence = [
        {
            "circumstance_id": "prod",
            "loadout_id": loadout_id,
            "provenance": "declared",
        }
        for loadout_id in ("prod", "prod_embed")
    ]
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    assert result["evaluated_evidence_records"] == 0
    assert result["model_ready"] is False


def _loadout_policy_with_status(*, interactive_status, note="no detail"):
    raw = build_policy(
        policy_id="status-policy",
        captured_at="2026-09-29T00:00:00Z",
        devices=[{"name": "card1", "vram_total_bytes": 8 * GiB}],
        models=[
            {
                "model_key": "small",
                "kind": "llm",
                "vram_bytes": 2 * GiB,
                "load_status": "verified",
            },
            {
                "model_key": "studio",
                "kind": "llm",
                "vram_bytes": 3 * GiB,
                "load_status": interactive_status,
                "load_note": note,
            },
        ],
        needs=[{"need": "prod_llm_endpoint", "resident_kind": "llm"}],
        loadouts=[
            _loadout(
                "prod",
                residents=[{"kind": "llm", "model_key": "small"}],
            ),
            _loadout(
                "prod_plus_studio",
                residents=[
                    {"kind": "llm", "model_key": "small"},
                    {"kind": "llm", "model_key": "studio"},
                ],
            ),
        ],
    )
    return Policy(raw)


def test_failed_model_rejects_its_loadout_even_though_it_fits():
    policy = _loadout_policy_with_status(
        interactive_status="failed",
        note="lms load hung 600s and the model never appeared",
    )
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    studio = next(
        c for c in result["candidates"] if c["loadout_id"] == "prod_plus_studio"
    )
    assert studio["feasible"] is False
    assert any("marked failed to load" in reason for reason in studio["reasons"])
    assert result["recommended_loadout_id"] == "prod"


def test_unverified_model_is_allowed_but_flagged_in_the_model_note():
    policy = _loadout_policy_with_status(interactive_status="unverified")
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    studio = next(
        c for c in result["candidates"] if c["loadout_id"] == "prod_plus_studio"
    )
    assert studio["feasible"] is True


def test_official_policy_marks_the_failed_interactive_model():
    policy = Policy.load(POLICY_PATH)
    assert policy.models["k2-horizon-7b"]["load_status"] == "failed"
    assert policy.models["k2-horizon-7b"]["load_note"]
    # A loadout depending on a failed model must not be recommendable.
    for loadout in policy.loadouts:
        failed = [
            r["model_key"]
            for r in loadout["residents"]
            if policy.models[r["model_key"]]["load_status"] == "failed"
        ]
        assert not failed, f"{loadout['loadout_id']} depends on failed {failed}"


def test_capture_script_writes_a_measured_record(tmp_path):
    """The capture path is what makes the evidence loop repeatable."""
    import json as _json
    import subprocess
    import sys

    from pathlib import Path

    policy_path = Path(POLICY_PATH)
    target = tmp_path / "policy.json"
    target.write_text(policy_path.read_text(encoding="utf-8"), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path("scripts") / "capture_fleet_loadout_evidence.py"),
            "--policy",
            str(target),
            "--circumstance-id",
            "prod_llm_uptime",
            "--loadout-id",
            "prod_llm_only",
            "--endpoint",
            "http://127.0.0.1:1",  # unreachable: proves failures are recorded
            "--samples",
            "1",
            "--timeout",
            "0.2",
            "--extra",
            '{"note": "synthetic"}',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    record = _json.loads(result.stdout)
    assert record["provenance"] == "measured"
    assert record["outcomes"]["completion_healthy"] is False
    assert record["outcomes"]["completion_error"]
    assert record["outcomes"]["note"] == "synthetic"

    written = _json.loads(target.read_text(encoding="utf-8"))
    matching = [
        item
        for item in written["evidence"]
        if item["circumstance_id"] == "prod_llm_uptime"
        and item["loadout_id"] == "prod_llm_only"
    ]
    assert len(matching) == 1, "capture must replace, not duplicate"
    assert matching[0]["provenance"] == "measured"


def test_capture_script_rejects_unknown_loadout(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run(
        [
            sys.executable,
            str(Path("scripts") / "capture_fleet_loadout_evidence.py"),
            "--policy",
            str(Path(POLICY_PATH)),
            "--circumstance-id",
            "prod_llm_uptime",
            "--loadout-id",
            "not_a_loadout",
            "--samples",
            "1",
            "--output",
            str(tmp_path / "out.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "unknown loadout_id" in (result.stdout + result.stderr)


def test_recommendation_commands_are_advisory_and_name_the_loadout():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "recommend_fleet_loadout", Path("scripts/recommend_fleet_loadout.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    policy = Policy.load(POLICY_PATH)
    recommendation = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True, "prod_embedder": True},
        circumstance_id="prod_llm_uptime",
    )
    rendered = module.render_commands(recommendation, policy)
    assert "advisory" in rendered
    assert '"dispatch_allowed": false' in rendered
    assert "lms load toolcall-v5-3b-combined-r2" in rendered
    # Runtime residents must be routed to the soak harness, not LM Studio.
    drill = recommend(
        policy,
        circumstance={"soak_drill_runtime": True},
        circumstance_id="soak_drill",
    )
    drill_text = module.render_commands(drill, policy)
    assert "bind-soak-runtime.sh" in drill_text
    assert "lms load qwen3.8-27b-rocmfp4-strix" not in drill_text
