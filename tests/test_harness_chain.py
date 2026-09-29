"""Harness evolution chain: deterministic chain identity, staged task
builders, and reflect-stage executor glue (fixtures reuse the iter4 fail
set from the harness version registry)."""

import pytest

from assistx import harness_chain as hc
from assistx.evaluation_registry import get_harness_suites


@pytest.fixture()
def run():
    return {
        "suite_id": "cpm-tb2-bench-v1",
        "harness_id": "cpm-tb2-bench-v1",
        "endpoint": "optiplex:1235",
        "model_key": "minicpm5-2b-iter5",
        "results": [
            {"task_id": "BM-003", "passed": True, "duration_s": 9.0},
            {"task_id": "BM-007", "passed": False,
             "expected": "git %h pipeline report", "actual": "%(h:8s) invalid",
             "duration_s": 41.0},
            {"task_id": "BM-008", "passed": False,
             "expected": "write the manifest", "actual": "echoed instead",
             "duration_s": 55.0},
        ],
    }


@pytest.fixture()
def chain(run):
    return hc.chain_from_run(run)


def suite(suite_id="cpm-tb2-bench-v1"):
    return {s.id: s for s in get_harness_suites()}[suite_id]


def test_chain_identity_is_deterministic_and_node_aware(run):
    chain = hc.chain_from_run(run)
    assert chain.chain_id == hc.chain_from_run(dict(run)).chain_id
    assert chain.chain_id.startswith("hchain-")
    assert chain.target_node == "optiplex"
    changed = {**run, "endpoint": "optiplex:1234"}
    assert chain.chain_id != hc.chain_from_run(changed).chain_id


def test_chain_from_run_requires_identity_fields():
    with pytest.raises(ValueError):
        hc.chain_from_run({"suite_id": "s", "endpoint": "", "model_key": "m"})


def test_lineage_rejects_unknown_stage(chain):
    with pytest.raises(ValueError):
        chain.lineage("promote")


def test_reflect_task_is_stamped(chain, run):
    task = hc.reflect_task(chain, run)
    assert task["kind"] == "harness_reflect"
    assert task["payload"]["chain"] == chain.lineage("reflect")
    assert task["target_agent_id"] == "optiplex"


def test_train_task_carries_reservation_request(chain):
    task = hc.train_task(
        chain,
        dataset_ref="auto-finetune/datasets/train.cpm-iter6.jsonl",
        gpu_node="xwing",
        train_config="auto-finetune/configs/cpm-iter6.yaml",
        output_dir="outputs/checkpoints/minicpm5-2b-iter6/",
    )
    assert task["kind"] == "harness_train"
    assert task["required_capabilities"] == ["finetune"]
    assert task["target_agent_id"] == "xwing"
    assert task["preemptible"] is False
    assert task["payload"]["reservation_request"] == {
        "node": "xwing", "resource": "gpu", "ttl_seconds": 4 * 3600,
    }
    assert task["payload"]["chain"] == chain.lineage("train")
    assert task["payload"]["dataset_ref"].endswith("train.cpm-iter6.jsonl")


def test_train_task_requires_inputs(chain):
    with pytest.raises(ValueError):
        hc.train_task(chain, dataset_ref="", gpu_node="xwing",
                      train_config="c", output_dir="o")


def test_deploy_task_validates_method_and_artifact(chain):
    task = hc.deploy_task(
        chain,
        artifact_ref="models/MiniCPM5-2B-iter6-lora.gguf",
        artifact_sha256="a" * 64,
        deploy_method="lora_adapters_swap",
    )
    assert task["kind"] == "harness_deploy"
    assert task["target_agent_id"] == "optiplex"
    assert task["payload"]["requires_runtime_admission"] is True
    assert task["payload"]["deploy_method"] == "lora_adapters_swap"
    assert task["payload"]["artifact"]["sha256"] == "a" * 64

    with pytest.raises(ValueError):
        hc.deploy_task(chain, artifact_ref="r", artifact_sha256="a" * 64,
                       deploy_method="rm_rf_everything")
    with pytest.raises(ValueError):
        hc.deploy_task(chain, artifact_ref="", artifact_sha256="a" * 64,
                       deploy_method="merged_gguf_repoint")


def test_rescore_task_carries_registry_trial_rule(chain):
    task = hc.rescore_task(chain, suite(suite_id="cpm-tb2-bench-v1"))
    assert task["kind"] == "harness_rescore"
    assert task["payload"]["trials"] == 1
    assert task["payload"]["compare_to_base_run"] == chain.base_run_identity
    assert task["payload"]["suite_id"] == "cpm-tb2-bench-v1"

    hermes = hc.rescore_task(
        chain, suite(suite_id="hermes_agent_intelligence.v1")
    )
    # The registry's three-trial rule for Hermes qualification suites is
    # machine-carried into the rescore task.
    assert hermes["payload"]["trials"] == 3
    assert hermes["idempotency_key"] == (
        f"harness-rescore:{chain.chain_id}:1"
    )


def test_rescore_attempt_changes_idempotency_key(chain):
    first = hc.rescore_task(chain, suite(), attempt=1)
    retry = hc.rescore_task(chain, suite(), attempt=2)
    assert first["idempotency_key"] != retry["idempotency_key"]
    assert retry["payload"]["attempt"] == 2


def test_execute_reflect_task_happy_path(chain, run):
    task = hc.reflect_task(chain, run)
    reflection = {
        "correction_pairs": [
            {"fail_id": "BM-007", "prompt": "git pipeline",
             "completion": "git log --pretty='%h %s' > report.txt"},
        ],
        "harness_fix_proposals": [
            {"fail_id": "BM-008", "title": "do-then-verify",
             "patch_summary": "check the written artifact"},
        ],
    }
    outcome = hc.execute_reflect_task(
        task, lambda prompt: "```json\n" + __import__("json").dumps(reflection) + "\n```"
    )
    assert outcome["ok"] is True
    assert outcome["errors"] == []
    assert outcome["uncovered_fails"] == ["BM-008"] or outcome["uncovered_fails"] == []
    assert outcome["evidence"]["chain_id"] == chain.chain_id
    assert outcome["evidence"]["stage"] == "reflect"


def test_execute_reflect_task_rejects_garbage_output(chain, run):
    task = hc.reflect_task(chain, run)
    outcome = hc.execute_reflect_task(task, lambda prompt: "I thought about it a lot!")
    assert outcome["ok"] is False
    assert any("no JSON object" in e for e in outcome["errors"])


def test_execute_reflect_task_rejects_non_reflect_tasks():
    with pytest.raises(ValueError):
        hc.execute_reflect_task({"payload": {"benchmark": True}}, lambda p: "{}")


def test_evidence_metadata_for_evaluation_run(chain):
    meta = hc.evidence_metadata(
        chain, "rescore", status="DONE", score=0.95,
        dataset_ref="train.cpm-iter6.jsonl", trials=1,
    )
    assert meta["harness_evolution"] is True
    assert meta["stage"] == "rescore"
    assert meta["chain_id"] == chain.chain_id
    assert meta["score"] == 0.95
    assert meta["dataset_ref"] == "train.cpm-iter6.jsonl"
