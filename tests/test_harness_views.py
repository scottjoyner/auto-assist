"""Harness evolution snapshot: chains from EvaluationRun lineage, live tasks,
and mistakes — the data behind /harness."""

import json

from assistx.harness_views import harness_evolution_snapshot


def eval_run(chain_id, stage, status, score=None, ts=1000, suite_id="cpm-tb2-bench-v1"):
    from assistx import harness_chain as hc
    from assistx import harness_reflect as hr

    chain = hc.HarnessChain(
        suite_id=suite_id,
        endpoint="optiplex:1235",
        model_key="minicpm5-2b-iter5",
        base_run_identity="deadbeef" * 4,
    )
    metadata = hc.evidence_metadata(chain, stage, status=status, score=score)
    metadata["chain_id"] = chain_id
    return {
        "id": f"run-{chain_id}-{stage}-{ts}",
        "status": status,
        "score": score,
        "created_at_ts": ts,
        "metadata_json": json.dumps(metadata),
    }


def reflect_task(chain_id, ts=2000, status="RUNNING"):
    return {
        "id": "task-reflect-1",
        "kind": "harness_reflect",
        "title": "Harness reflect cpm-tb2-bench-v1 on optiplex:1235",
        "status": status,
        "target_agent_id": "optiplex",
        "updated_at_ts": ts,
        "payload_json": json.dumps({
            "harness_reflect": True,
            "suite_id": "cpm-tb2-bench-v1",
            "endpoint": "optiplex:1235",
            "chain": {"chain_id": chain_id, "stage": "reflect"},
            "fail_set": [
                {"task_id": "BM-007", "expected": "git %h pipeline",
                 "actual": "%(h:8s) invalid specifier"},
            ],
        }),
    }


def test_snapshot_groups_chains_and_stages_in_order():
    runs = [
        eval_run("hchain-a", "rescore", "DONE", score=1.0, ts=4000),
        eval_run("hchain-a", "reflect", "DONE", ts=1000),
        eval_run("hchain-a", "train", "DONE", ts=2000),
        eval_run("hchain-a", "deploy", "DONE", ts=3000),
        # Non-harness evaluation runs must not leak in.
        {"id": "other", "status": "DONE", "score": 0.1, "metadata_json": "{}"},
    ]
    snapshot = harness_evolution_snapshot(runs, [])
    assert snapshot["chain_count"] == 1
    chain = snapshot["chains"][0]
    assert chain["chain_id"] == "hchain-a"
    assert [s["stage"] for s in chain["stages"]] == [
        "reflect", "train", "deploy", "rescore",
    ]
    assert chain["score"] == 1.0
    assert snapshot["chains"][0]["has_live_task"] is False


def test_snapshot_surfaces_live_tasks_and_mistakes():
    tasks = [
        reflect_task("hchain-a"),
        {
            "id": "task-deploy-1",
            "kind": "harness_deploy",
            "title": "Harness deploy minicpm5-2b-iter6 to optiplex:1235",
            "status": "FAILED",
            "target_agent_id": "optiplex",
            "updated_at_ts": 3000,
            "last_error": "llama.cpp /lora-adapters returned 500",
            "payload_json": json.dumps({
                "harness_deploy": True,
                "endpoint": "optiplex:1235",
                "chain": {"chain_id": "hchain-a", "stage": "deploy"},
            }),
        },
        # Ordinary tasks never appear in the harness view.
        {"id": "t1", "kind": "agent_task", "title": "unrelated",
         "status": "READY", "updated_at_ts": 5000, "payload_json": "{}"},
    ]
    snapshot = harness_evolution_snapshot([], tasks)
    kinds = [t["kind"] for t in snapshot["live_tasks"]]
    assert kinds == ["harness_deploy", "harness_reflect"]  # newest first
    deploy = snapshot["live_tasks"][0]
    assert deploy["response"] == "llama.cpp /lora-adapters returned 500"
    assert deploy["objective"].startswith("Harness deploy")

    mistakes = snapshot["mistakes"]
    by_task = {m["task_id"]: m for m in mistakes}
    assert by_task["BM-007"]["expected"] == "git %h pipeline"
    assert by_task["BM-007"]["chain_id"] == "hchain-a"
    assert "lora-adapters returned 500" in by_task[
        "Harness deploy minicpm5-2b-iter6 to optiplex:1235"
    ]["actual"]


def test_snapshot_handles_string_metadata_and_empty_input():
    assert harness_evolution_snapshot([], []) == {
        "chains": [], "live_tasks": [], "mistakes": [],
        "task_count": 0, "chain_count": 0,
    }
    snapshot = harness_evolution_snapshot(
        [{"metadata_json": "{not json"}], [{"payload_json": None, "kind": "harness_train"}]
    )
    assert snapshot["chain_count"] == 0
    assert snapshot["live_tasks"][0]["kind"] == "harness_train"
    assert snapshot["live_tasks"][0]["chain_id"] is None
