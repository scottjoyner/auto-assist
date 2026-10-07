import importlib.util
import json
import os
import pathlib
from unittest import mock

import pytest


MODULE_PATH = pathlib.Path(__file__).parent.parent / "scripts" / "launch_free_subagent.py"
SPEC = importlib.util.spec_from_file_location("launch_free_subagent", MODULE_PATH)
selector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(selector)


def projection(*, proven=None, trial=None, zai_policy="reserve_only"):
    return {
        "schema": "free-pool-projection/v1",
        "read_only": True,
        "authoritative_routing_unchanged": True,
        "zai_policy": zai_policy,
        "kilo": {
            "proven_exact_models": list(proven or []),
            "trial_exact_models": list(trial or []),
            "blocked_low_fitness_exact_models": [],
        },
    }


def write_projection(tmp_path, payload):
    path = tmp_path / "projection.json"
    path.write_text(json.dumps(payload))
    return path


def test_proven_model_is_selected_before_trial():
    result = selector.select_model(
        projection(proven=["proven/model:free"], trial=["trial/model:free"]),
        allow_trial=True,
    )
    assert result["model"] == "proven/model:free"
    assert result["tier"] == "proven"
    assert result["budget"]["max_input_tokens"] > selector.BUDGET_PROFILES["trial"]["max_input_tokens"]


def test_trial_requires_explicit_opt_in():
    payload = projection(trial=["trial/model:free"])
    with pytest.raises(ValueError, match="--allow-trial"):
        selector.select_model(payload)
    selected = selector.select_model(payload, allow_trial=True)
    assert selected["model"] == "trial/model:free"
    assert selected["tier"] == "trial"


def test_preferred_model_must_be_eligible():
    payload = projection(proven=["good/model:free"], trial=["new/model:free"])
    with pytest.raises(ValueError, match="not eligible"):
        selector.select_model(payload, preferred_model="blocked/model:free")
    with pytest.raises(ValueError, match="not eligible"):
        selector.select_model(payload, preferred_model="new/model:free")
    selected = selector.select_model(
        payload,
        allow_trial=True,
        preferred_model="new/model:free",
    )
    assert selected["tier"] == "trial"


def test_projection_must_be_fresh(tmp_path):
    path = write_projection(tmp_path, projection(proven=["good/model:free"]))
    os.utime(path, (100.0, 100.0))
    with pytest.raises(ValueError, match="stale"):
        selector.load_projection(path, max_age_seconds=60.0, now=1000.0)


def test_projection_preserves_zai_reserve_boundary(tmp_path):
    path = write_projection(
        tmp_path,
        projection(proven=["good/model:free"], zai_policy="usable"),
    )
    with pytest.raises(ValueError, match="reserve_only"):
        selector.load_projection(path, max_age_seconds=0)


def test_trial_budget_is_stricter_than_proven():
    proven = selector.BUDGET_PROFILES["proven"]
    trial = selector.BUDGET_PROFILES["trial"]
    assert trial["max_wall_seconds"] < proven["max_wall_seconds"]
    assert trial["max_input_tokens"] < proven["max_input_tokens"]
    assert trial["max_reasoning_tokens"] < proven["max_reasoning_tokens"]
    assert trial["max_tool_calls"] < proven["max_tool_calls"]
    assert trial["max_no_progress_input_tokens"] < proven["max_no_progress_input_tokens"]


def test_launch_selected_passes_projection_tier_budget_to_exact_launcher(tmp_path, monkeypatch):
    model = "good/model:free"
    projection_path = write_projection(tmp_path, projection(proven=[model]))
    state_root = tmp_path / "state"
    worktree = tmp_path / "wt"
    worktree.mkdir()

    class FakeLauncher:
        @staticmethod
        def opencode_db_default():
            return tmp_path / "opencode.db"

        @staticmethod
        def launch(**kwargs):
            return {
                "success": True,
                "requested_model": kwargs["model_id"],
                "received": kwargs,
            }

    monkeypatch.setattr(selector, "_load_launcher", lambda: FakeLauncher)
    result = selector.launch_selected(
        projection_path=projection_path,
        objective="do useful work",
        worktree=worktree,
        state_root=state_root,
        max_projection_age_seconds=0,
    )
    assert result["requested_model"] == model
    assert result["selection_tier"] == "proven"
    received = result["received"]
    assert received["max_input_tokens"] == selector.BUDGET_PROFILES["proven"]["max_input_tokens"]
    assert received["max_no_progress_input_tokens"] == selector.BUDGET_PROFILES["proven"]["max_no_progress_input_tokens"]
    assert received["state_path"] == state_root / "live.jsonl"
    assert received["trace_dir"] == state_root / "traces"


def test_no_candidates_fails_closed():
    with pytest.raises(ValueError, match="no eligible"):
        selector.select_model(projection())
