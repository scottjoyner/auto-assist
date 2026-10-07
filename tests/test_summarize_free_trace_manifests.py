from __future__ import annotations

import importlib.util
import pathlib


MODULE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "scripts"
    / "summarize_free_trace_manifests.py"
)
SPEC = importlib.util.spec_from_file_location("trace_scorecard", MODULE_PATH)
trace_scorecard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(trace_scorecard)


def manifest(model: str, *, status: str = "completed", failure=None, seconds=10):
    return {
        "schema": "subagent-run-manifest/v1",
        "effective_model": model,
        "requested_model": model,
        "status": status,
        "failure_class": failure,
        "model_identity_complete": True,
        "cost": 0.0,
        "launch_timestamp": "2026-10-07T20:00:00Z",
        "finished_at": f"2026-10-07T20:00:{seconds:02d}Z",
        "final_git": {"is_git": True, "dirty_paths": 0},
        "raw_error_summary": {
            "count": 0 if status == "completed" else 1,
            "classes": {} if status == "completed" else {failure: 1},
            "status_codes": {},
        },
        "patch_parts": 0,
        "last_finish_reason": "stop" if status == "completed" else "tool-calls",
        "tokens": {
            "input": 100,
            "output": 20,
            "reasoning": 5,
            "cache_read": 10,
            "cache_write": 0,
        },
        "tools": {"read": 2},
    }


def test_score_run_full_evidence_is_100():
    assert trace_scorecard.score_run(manifest("model-a")) == 100


def test_failed_run_scores_below_completed_run():
    good = manifest("model-a")
    bad = manifest("model-b", status="failed", failure="timeout")
    assert trace_scorecard.score_run(bad) < trace_scorecard.score_run(good)


def test_missing_raw_error_summary_is_unverified_not_clean():
    legacy = manifest("model-a")
    legacy.pop("raw_error_summary")
    assert trace_scorecard.score_run(legacy) == 95


def test_summarize_ranks_reliable_model_first_and_aggregates():
    records = [
        manifest("model-a", seconds=10),
        manifest("model-a", seconds=20),
        manifest("model-b", status="failed", failure="timeout", seconds=15),
    ]
    result = trace_scorecard.summarize(records)
    assert result["runs"] == 3
    assert result["completed"] == 2
    assert result["failed"] == 1
    assert result["total_cost"] == 0.0
    assert result["tokens"]["input"] == 300
    assert result["tools"] == {"read": 6}
    assert result["models"][0]["model"] == "model-a"
    assert result["models"][0]["success_rate"] == 1.0
    assert result["models"][0]["average_duration_seconds"] == 15.0
    assert result["models"][1]["failure_classes"] == {"timeout": 1}
