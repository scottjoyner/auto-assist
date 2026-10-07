#!/usr/bin/env python3
"""Focused tests for free_pool_status CLI."""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

from free_pool_status import (
    classify_models,
    classify_runs,
    load_projection,
    build_status,
)

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"


def test_classify_models_splits_tiers():
    projection = {
        "kilo": {
            "models": [
                {"id": "m1", "recommendation_tier": "proven"},
                {"id": "m2", "recommendation_tier": "trial"},
                {"id": "m3", "recommendation_tier": "blocked_low_fitness"},
                {"id": "m4", "recommendation_tier": "blocked_unavailable"},
            ]
        }
    }
    tiers = classify_models(projection)
    assert tiers["proven"] == ["m1"]
    assert tiers["trial"] == ["m2"]
    assert tiers["blocked_low_fitness"] == ["m3"]
    assert tiers["blocked_unavailable"] == ["m4"]


def test_classify_runs_identifies_active_failed_budget():
    records = [
        {
            "provider": "kilo_free",
            "model": "m1",
            "session": "s1",
            "pid": 111,
            "worktree": "/tmp/wt1",
            "status": "running",
            "failure_class": "",
            "tokens": {"input": 100},
            "tools": {"bash": 1},
            "title": "t1",
            "objective": "",
            "created_at": "2026-10-07T00:00:00Z",
            "updated_at": "2026-10-07T01:00:00Z",
            "started_at": "2026-10-07T00:00:00Z",
        },
        {
            "provider": "kilo_free",
            "model": "m2",
            "session": "s2",
            "pid": 222,
            "worktree": "/tmp/wt2",
            "status": "failed",
            "failure_class": "rate_limited",
            "tokens": {"input": 200},
            "tools": {"read": 2},
            "title": "t2",
            "objective": "budget test",
            "created_at": "2026-10-07T00:00:00Z",
            "updated_at": "2026-10-07T01:00:00Z",
        },
    ]
    runs = classify_runs(records, process_alive=lambda record: True)
    assert len(runs["active"]) == 1
    assert runs["active"][0]["model"] == "m1"
    assert len(runs["failed"]) == 1
    assert runs["failed"][0]["model"] == "m2"
    assert len(runs["budget_terminations"]) == 1
    assert runs["budget_terminations"][0]["model"] == "m2"


def test_load_projection_from_file():
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tf:
        path = pathlib.Path(tf.name)
    try:
        payload = {"kilo": {"models": []}, "zai_policy": "reserve_only"}
        path.write_text(json.dumps(payload))
        result = load_projection(path)
        assert result["zai_policy"] == "reserve_only"
    finally:
        path.unlink(missing_ok=True)


def test_load_projection_missing():
    result = load_projection(pathlib.Path("/definitely/missing.json"))
    assert "error" in result


def test_build_status_readonly():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        projection = {
            "schema": "free-pool-projection/v1",
            "zai_policy": "reserve_only",
            "kilo": {"models": []},
        }
        (root / "free-pool-projection.json").write_text(json.dumps(projection))
        (root / "live.jsonl").write_text("")
        (root / "history.jsonl").write_text("")
        (root / "provider-qualification.jsonl").write_text("")

        result = build_status(state_root=root)
        assert result["read_only"] is True
        assert result["zai_reserve_policy"] == "reserve_only"
        assert "models" in result
        assert "runs" in result
        assert result["evidence_fresh"] is False


def test_build_status_live_only():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        projection = {
            "schema": "free-pool-projection/v1",
            "zai_policy": "reserve_only",
            "kilo": {"models": []},
        }
        (root / "free-pool-projection.json").write_text(json.dumps(projection))
        (root / "live.jsonl").write_text(json.dumps({
            "provider": "kilo_free",
            "model": "m1",
            "session": "s1",
            "pid": 111,
            "worktree": "/tmp/wt1",
            "status": "running",
            "failure_class": "",
            "tokens": {},
            "tools": {},
            "title": "t1",
            "objective": "",
            "created_at": "2026-10-07T00:00:00Z",
            "updated_at": "2026-10-07T01:00:00Z",
            "started_at": "2026-10-07T00:00:00Z",
        }) + "\n")
        (root / "history.jsonl").write_text("")
        (root / "provider-qualification.jsonl").write_text("")

        result = build_status(state_root=root, live_only=True, process_alive=lambda record: True)
        assert len(result["runs"]["active"]) == 1
        assert result["runs"]["active"][0]["model"] == "m1"


def test_classify_runs_active_process_alive():
    records = [{
        "provider": "kilo_free",
        "model": "m1",
        "session": "s1",
        "pid": 111,
        "worktree": "/tmp/wt1",
        "status": "running",
        "failure_class": "",
        "tokens": {},
        "tools": {},
        "title": "t1",
        "objective": "",
        "created_at": "2026-10-07T00:00:00Z",
        "updated_at": "2026-10-07T01:00:00Z",
        "started_at": "2026-10-07T00:00:00Z",
    }]
    runs = classify_runs(records, process_alive=lambda record: True)
    assert len(runs["active"]) == 1
    assert runs["active"][0]["process_alive"] is True


if __name__ == "__main__":
    test_classify_models_splits_tiers()
    test_classify_runs_identifies_active_failed_budget()
    test_load_projection_from_file()
    test_load_projection_missing()
    test_build_status_readonly()
    test_build_status_live_only()
    test_classify_runs_active_process_alive()
    print("all free_pool_status tests passed")