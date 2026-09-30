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


def _device_exclusive_policy():
    raw = build_policy(
        policy_id="exclusive-policy",
        captured_at="2026-09-30T00:00:00Z",
        devices=[{"name": "card1", "vram_total_bytes": 32 * GiB}],
        models=[
            {"model_key": "prod", "kind": "llm", "vram_bytes": 6 * GiB},
            {
                "model_key": "big",
                "kind": "llm",
                "vram_bytes": 22 * GiB,
                "exclusive_gpu": True,
            },
        ],
        needs=[{"need": "prod_llm_endpoint", "resident_kind": "llm"}],
        loadouts=[
            _loadout("prod", residents=[{"kind": "llm", "model_key": "prod"}]),
            _loadout(
                "prod_plus_big",
                residents=[
                    {"kind": "llm", "model_key": "prod"},
                    {"kind": "llm", "model_key": "big"},
                ],
            ),
            _loadout("big_only", residents=[{"kind": "llm", "model_key": "big"}]),
        ],
    )
    return Policy(raw)


def test_device_exclusive_resident_cannot_share_the_gpu():
    """22 + 6 GiB fits inside 32 GiB, and the runtime still refuses it.

    Capacity arithmetic said the combined loadout was fine; loading the big
    model next to the production model failed with "unable to allocate
    ROCm0 buffer". That constraint is a device property, not a sum.
    """
    policy = _device_exclusive_policy()
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    combined = next(
        c for c in result["candidates"] if c["loadout_id"] == "prod_plus_big"
    )
    assert combined["feasible"] is False
    assert any("require the device to themselves" in r for r in combined["reasons"])


def test_device_exclusive_resident_alone_is_feasible():
    policy = _device_exclusive_policy()
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    alone = next(c for c in result["candidates"] if c["loadout_id"] == "big_only")
    assert alone["feasible"] is True


def test_official_policy_encodes_the_measured_exclusivity():
    policy = Policy.load(POLICY_PATH)
    big = policy.models["ornith-1.5-35b-a3b-apex-mtp"]
    assert big["load_status"] == "verified", "the 35B does load, alone"
    assert big["exclusive_gpu"] is True
    assert big["vram_bytes"] > 20 * GiB, "resident cost, not file size"


def _role_policy():
    raw = build_policy(
        policy_id="role-policy",
        captured_at="2026-09-30T00:00:00Z",
        devices=[{"name": "card1", "vram_total_bytes": 32 * GiB}],
        models=[
            {"model_key": "prod", "kind": "llm", "vram_bytes": 6 * GiB},
            {
                "model_key": "big",
                "kind": "llm",
                "vram_bytes": 22 * GiB,
                "exclusive_gpu": True,
            },
        ],
        needs=[
            {
                "need": "prod_llm_endpoint",
                "resident_kind": "llm",
                "role": "prod-llm",
            },
            {
                "need": "interactive_lane",
                "resident_kind": "llm",
                "role": "interactive",
            },
        ],
        loadouts=[
            _loadout(
                "prod_only",
                residents=[
                    {"kind": "llm", "model_key": "prod", "role": "prod-llm"}
                ],
                preferred_when=["prod"],
            ),
            _loadout(
                "interactive_only",
                residents=[
                    {"kind": "llm", "model_key": "big", "role": "interactive"}
                ],
            ),
        ],
    )
    return Policy(raw)


def test_role_pinned_need_is_not_satisfied_by_the_wrong_role():
    """The production model is an `llm`, but it is not an interactive lane.

    Matching needs on kind alone made the recommender answer
    "prod_llm_only" for a circumstance that explicitly wanted a studio lane.
    """
    policy = _role_policy()
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True, "interactive_lane": True},
        circumstance_id="prod",
    )
    prod_only = next(
        c for c in result["candidates"] if c["loadout_id"] == "prod_only"
    )
    assert prod_only["feasible"] is False
    assert any("role 'interactive'" in reason for reason in prod_only["reasons"])
    assert result["recommended_loadout_id"] is None, (
        "no loadout serves both role-pinned needs on one card; the "
        "recommender must say so rather than drop a need silently"
    )
    # And the big model must not masquerade as the production endpoint.
    interactive = next(
        c for c in result["candidates"] if c["loadout_id"] == "interactive_only"
    )
    assert interactive["feasible"] is False
    assert any("role 'prod-llm'" in reason for reason in interactive["reasons"])


def test_unpinned_need_still_matches_on_kind_alone():
    policy = _role_policy()
    result = recommend(
        policy,
        circumstance={"prod_llm_endpoint": True},
        circumstance_id="prod",
    )
    prod_only = next(
        c for c in result["candidates"] if c["loadout_id"] == "prod_only"
    )
    assert prod_only["feasible"] is True


def test_official_interactive_need_is_role_pinned():
    policy = Policy.load(POLICY_PATH)
    assert policy.needs["interactive_lane"]["role"] == "interactive"
    # Every loadout claiming to serve an interactive lane must say so by role.
    pinned = {name for name, spec in policy.needs.items() if spec.get("role")}
    assert "interactive_lane" in pinned
    assert "prod_llm_endpoint" in pinned
    # A loadout that claims an interactive lane must actually label one.
    for loadout in policy.loadouts:
        roles = {r.get("role") for r in loadout["residents"]}
        assert roles - {"prod-llm", "prod-embed", "drill", "interactive"} == set()


def test_infeasible_recommendation_lists_what_each_option_would_break():
    """A null answer is only useful if it is actionable."""
    result = recommend(
        _role_policy(),
        circumstance={"prod_llm_endpoint": True, "interactive_lane": True},
        circumstance_id="prod",
    )
    assert result["recommended_loadout_id"] is None
    assert result["decision_basis"] == "infeasible"
    assert result["blocking"], "an infeasible verdict must explain itself"
    blocked = {item["loadout_id"] for item in result["blocking"]}
    assert blocked == {"prod_only", "interactive_only"}
    for item in result["blocking"]:
        assert item["reasons"], f"{item['loadout_id']} blocked with no reason"
    assert "relax" in result["note"]


def test_forced_decision_is_reported_separately_from_readiness():
    """One feasible option means the model has nothing to choose between.

    prod_llm_uptime has exactly one feasible loadout (the interactive
    co-residency is measured infeasible), so no amount of evidence would hand
    the decision to the model. That should be visible, not look like a gate
    still waiting on data.
    """
    result = recommend(
        Policy.load(POLICY_PATH),
        circumstance={"prod_llm_endpoint": True, "prod_embedder": True},
        circumstance_id="prod_llm_uptime",
    )
    assert result["decision_is_forced"] is True
    assert "forced by feasibility" in result["model_note"]
    assert result["model_scoring_active"] is False


def test_multiple_feasible_options_are_not_forced():
    policy = _policy()
    result = recommend(
        policy,
        circumstance={},
        circumstance_id="gpu_idle",
    )
    assert result["decision_is_forced"] is False


def test_verification_provenance_is_recorded_per_model():
    """Loadability in one runtime is not proof for another."""
    policy = Policy.load(POLICY_PATH)
    drill = policy.models["qwen3.8-27b-rocmfp4-strix"]
    assert drill["load_status"] == "verified"
    assert "soak-binder" in drill["verified_in"]
    assert "LM Studio" in drill["load_note"], "must not imply LM Studio proof"
    prod = policy.models["toolcall-v5-3b-combined-r2"]
    assert prod["verified_in"] == "lm-studio"


def test_cheap_model_is_recorded_as_failing_the_quality_gate():
    """The obvious cost lever for the ladder was measured, not assumed.

    A 0.8B model loads instantly and serves completions, but passed 1 of 8
    representative cases. The suite gate requires every case to pass, so it is
    not a viable policy. The policy file must keep that fact, with its reason,
    so nobody re-proposes the cheap runtime without the quality caveat.
    """
    policy = Policy.load(POLICY_PATH)
    small = policy.models["qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled"]
    assert small["load_status"] == "verified", "it does load; that is not the issue"
    assert "1 of 8" in small["load_note"]
    assert "not a viable policy" in small["load_note"].lower()
