import importlib.util
import json
import pathlib

MODULE_PATH = pathlib.Path(__file__).parent.parent / "scripts" / "free_pool_projection.py"
SPEC = importlib.util.spec_from_file_location("free_pool_projection", MODULE_PATH)
projection = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(projection)


def catalog_record(model_id, *, tools=True, context=262144):
    params = ["max_tokens", "temperature"]
    if tools:
        params += ["tools", "tool_choice"]
    return {
        "id": model_id,
        "pricing": {"prompt": "0", "completion": "0"},
        "context_length": context,
        "supported_parameters": params,
    }


def qualification(model_id, *, qualified=True, status=200, started="2026-10-06T22:00:00Z"):
    row = {
        "schema": "free-provider-qualification/v1",
        "provider": "kilo_free",
        "requested_model": model_id,
        "started_at": started,
        "http_status": status,
        "qualified": qualified,
    }
    if qualified:
        row.update(
            {
                "response_model": model_id,
                "structured_tool_call": True,
                "model_identity_complete": True,
                "cost": 0,
                "upstream_inference_cost": 0,
            }
        )
    return row


def state_record(
    model_id,
    *,
    status="completed",
    title="coding-run",
    updated="2026-10-06T22:01:00Z",
    failure=None,
    patches=1,
):
    return {
        "provider": "kilo_free",
        "requested_model": model_id,
        "effective_model": model_id,
        "model_identity_complete": True,
        "route_kind": "exact",
        "status": status,
        "title": title,
        "updated_at": updated,
        "failure_class": failure,
        "patch_parts": patches,
        "tools": {"bash": 1, "edit": 1} if patches else {"read": 2},
        "tokens": {"input": 100, "output": 20},
        "launch_git": {"head": "a", "dirty_paths": 0},
        "final_git": {"head": "b" if patches else "a", "dirty_paths": 0},
    }


def test_qualified_usable_tool_model_is_recommended():
    model = "poolside/laguna-s-2.1:free"
    result = projection.build_projection(
        catalog_records=[catalog_record(model)],
        qualification_records=[qualification(model)],
        state_records=[state_record(model)],
        credential_presence={},
    )
    row = result["kilo"]["models"][0]
    assert row["capability_qualified"] is True
    assert row["capacity_state"] == "usable"
    assert row["eligible_for_recommendation"] is True
    assert result["kilo"]["recommended_exact_models"] == [model]
    assert result["providers"]["kilo_anonymous"]["state"] == "usable"


def test_latest_rate_limit_blocks_capability_qualified_model():
    model = "inclusionai/ling-3.1-flash"
    quals = [
        qualification(model, started="2026-10-06T22:00:00Z"),
        qualification(
            model,
            qualified=False,
            status=429,
            started="2026-10-06T22:05:00Z",
        ),
    ]
    result = projection.build_projection(
        catalog_records=[catalog_record(model)],
        qualification_records=quals,
        state_records=[],
        credential_presence={},
    )
    row = result["kilo"]["models"][0]
    assert row["capability_qualified"] is True
    assert row["capacity_state"] == "rate_limited"
    assert row["eligible_for_recommendation"] is False


def test_busy_model_is_not_recommended():
    model = "cohere/north-mini-code:free"
    busy = state_record(model, status="running", updated="2026-10-06T22:10:00Z")
    result = projection.build_projection(
        catalog_records=[catalog_record(model)],
        qualification_records=[qualification(model)],
        state_records=[busy],
        credential_presence={},
        process_alive=lambda _record: True,
    )
    assert result["kilo"]["models"][0]["capacity_state"] == "busy"
    assert result["kilo"]["recommended_exact_models"] == []


def test_zero_cost_non_tool_model_is_not_coding_recommended():
    model = "google/lyria-3-pro-preview"
    result = projection.build_projection(
        catalog_records=[catalog_record(model, tools=False)],
        qualification_records=[],
        state_records=[],
        credential_presence={},
    )
    row = result["kilo"]["models"][0]
    assert row["supports_tools"] is False
    assert row["eligible_for_recommendation"] is False


def test_retired_qualified_identity_is_preserved():
    old = "inclusionai/ling-old:free"
    result = projection.build_projection(
        catalog_records=[catalog_record("poolside/laguna-s-2.1:free")],
        qualification_records=[qualification(old)],
        state_records=[],
        credential_presence={},
    )
    assert result["kilo"]["retired_catalog_missing"] == [old]


def test_coding_fitness_is_separate_from_canary_fitness():
    model = "dots-studio/dots-3-note-preview:free"
    states = [
        state_record(model, title="dots-canary", patches=0),
        state_record(
            model,
            status="stalled_abandoned",
            title="dots-coding",
            updated="2026-10-06T22:02:00Z",
            patches=0,
        ),
    ]
    grouped = projection.model_fitness_projection(states)
    assert grouped[("kilo_free", model, "canary")]["success_rate"] == 1.0
    assert grouped[("kilo_free", model, "coding")]["no_progress_rate"] == 1.0


def test_stale_running_record_does_not_keep_model_busy():
    model = "cohere/north-mini-code:free"
    stale = state_record(model, status="running", updated="2026-10-06T22:10:00Z")
    stale["pid"] = 999999
    result = projection.build_projection(
        catalog_records=[catalog_record(model)],
        qualification_records=[qualification(model)],
        state_records=[stale],
        credential_presence={},
        process_alive=lambda _record: False,
    )
    row = result["kilo"]["models"][0]
    assert row["capacity_state"] == "usable"
    assert row["eligible_for_recommendation"] is True


def test_projection_output_is_private(tmp_path):
    root = tmp_path / "state"
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"data": [catalog_record("poolside/laguna-s-2.1:free")]})
    )
    output = root / "projection.json"
    assert projection.main(
        [
            "--state-root",
            str(root),
            "--catalog-fixture",
            str(catalog),
            "--output",
            str(output),
        ]
    ) == 0
    assert output.stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700


def test_unconfigured_first_party_provider_stays_unconfigured():
    result = projection.build_projection(
        catalog_records=[],
        qualification_records=[],
        state_records=[],
        credential_presence={},
    )
    assert result["providers"]["groq"]["state"] == "unconfigured"
    assert result["providers"]["nvidia_nim"]["state"] == "unconfigured"
    assert result["providers"]["zai"]["state"] == "reserve_only"
    assert result["providers"]["cerebras"]["state"] == "payment_required"


def test_newly_qualified_model_is_trial_until_it_earns_coding_fitness():
    model = "inclusionai/ling-3.1-flash"
    result = projection.build_projection(
        catalog_records=[catalog_record(model)],
        qualification_records=[qualification(model)],
        state_records=[],
        credential_presence={},
    )
    row = result["kilo"]["models"][0]
    assert row["recommendation_tier"] == "trial"
    assert row["eligible_for_recommendation"] is True
    assert result["kilo"]["trial_exact_models"] == [model]
    assert result["kilo"]["proven_exact_models"] == []


def test_low_fitness_healthy_model_is_blocked_from_coding_recommendation():
    model = "dots-studio/dots-3-note-preview:free"
    states = [
        state_record(
            model,
            status="stalled_abandoned",
            title="dots-coding",
            patches=0,
        )
    ]
    result = projection.build_projection(
        catalog_records=[catalog_record(model)],
        qualification_records=[qualification(model)],
        state_records=states,
        credential_presence={},
    )
    row = result["kilo"]["models"][0]
    assert row["capacity_state"] == "usable"
    assert row["recommendation_tier"] == "blocked_low_fitness"
    assert row["eligible_for_recommendation"] is False
    assert result["kilo"]["recommended_exact_models"] == []
    assert result["kilo"]["blocked_low_fitness_exact_models"] == [model]


def test_proven_models_sort_before_trials():
    proven = "poolside/laguna-s-2.1:free"
    trial = "inclusionai/ling-3.1-flash"
    result = projection.build_projection(
        catalog_records=[catalog_record(trial), catalog_record(proven)],
        qualification_records=[qualification(trial), qualification(proven)],
        state_records=[state_record(proven)],
        credential_presence={},
    )
    assert result["kilo"]["proven_exact_models"] == [proven]
    assert result["kilo"]["trial_exact_models"] == [trial]
    assert result["kilo"]["recommended_exact_models"] == [proven, trial]
