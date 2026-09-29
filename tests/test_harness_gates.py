"""Promotion gates: registry rules as machine checks over rescore evidence."""

import pytest

from assistx import harness_gates as hg
from assistx.evaluation_registry import get_harness_suites


@pytest.fixture()
def chain(run):
    from assistx import harness_chain as hc

    return hc.chain_from_run(run)


@pytest.fixture()
def run():
    return {
        "suite_id": "cpm-tb2-bench-v1",
        "harness_id": "cpm-tb2-bench-v1",
        "endpoint": "optiplex:1235",
        "model_key": "minicpm5-2b-iter5",
        "results": [
            {"task_id": "BM-007", "passed": False,
             "expected": "git %h pipeline", "actual": "%(h:8s) invalid"},
        ],
    }


def rescore_run(chain, score, status="DONE", stage="rescore", chain_id=None):
    from assistx import harness_chain as hc

    return {
        "id": f"eval-{score}-{status}",
        "status": status,
        "score": score,
        "agent_class": "harness",
        "metadata": {
            **hc.evidence_metadata(chain, stage, status=status),
            "chain_id": chain_id or chain.chain_id,
        },
    }


def good_dataset():
    return {
        "ref": "auto-finetune/datasets/train.cpm-iter6.jsonl",
        "source_session_ids": ["hermes-sess-1", "hermes-sess-2"],
        "redacted": True,
        "deduplicated": True,
        "held_out_split": True,
    }


def suite(suite_id="cpm-tb2-bench-v1"):
    return {s.id: s for s in get_harness_suites()}[suite_id]


def test_all_gates_pass_promotes(chain, run):
    runs = [
        rescore_run(chain, 1.0),
        rescore_run(chain, 0.9),
    ]
    decision = hg.evaluate_promotion(
        chain, suite(), runs, dataset=good_dataset(), incumbent_score=0.95,
    )
    assert decision.promote is True
    assert decision.reasons == []
    assert all(decision.checks.values())


def test_trials_met_requires_min_trials(chain):
    decision = hg.evaluate_promotion(chain, suite(), [], dataset=good_dataset())
    assert decision.promote is False
    assert decision.checks["trials_met"] is False
    assert any("0 valid rescore run(s)" in r for r in decision.reasons)


def test_hermes_suites_demand_three_trials(chain):
    runs = [rescore_run(chain, 0.9), rescore_run(chain, 0.9)]
    decision = hg.evaluate_promotion(
        chain, suite(suite_id="hermes_agent_intelligence.v1"), runs,
        dataset=good_dataset(),
    )
    assert decision.checks["trials_met"] is False
    assert any("3 required" in r for r in decision.reasons)


def test_incomplete_runs_fail_runs_ok(chain):
    runs = [
        rescore_run(chain, 1.0),
        rescore_run(chain, None, status="RUNNING"),
    ]
    decision = hg.evaluate_promotion(chain, suite(), runs, dataset=good_dataset())
    assert decision.checks["runs_ok"] is False
    assert any("incomplete or unscored" in r for r in decision.reasons)


def test_score_floor_gate(chain):
    runs = [rescore_run(chain, 0.5), rescore_run(chain, 0.6)]
    decision = hg.evaluate_promotion(chain, suite(), runs, dataset=good_dataset())
    assert decision.checks["score_floor"] is False
    assert any("below floor" in r for r in decision.reasons)


def test_no_regression_gate(chain):
    runs = [rescore_run(chain, 0.85)]
    decision = hg.evaluate_promotion(
        chain, suite(), runs, dataset=good_dataset(), incumbent_score=0.9,
    )
    assert decision.checks["no_regression"] is False
    assert any("regression" in r for r in decision.reasons)


def test_trace_first_gate_rejects_manual_sketches(chain):
    runs = [rescore_run(chain, 1.0), rescore_run(chain, 1.0)]
    # The hernes_v1_train.jsonl pattern: a manually authored design sketch.
    sketch = {"ref": "auto-finetune/datasets/hernes_v1_train.jsonl"}
    decision = hg.evaluate_promotion(chain, suite(), runs, dataset=sketch)
    assert decision.promote is False
    assert decision.checks["dataset_trace_gate"] is False
    assert any("source session ids" in r for r in decision.reasons)
    assert any("held-out" in r for r in decision.reasons)


def test_trace_first_gate_requires_all_four_items(chain):
    runs = [rescore_run(chain, 1.0), rescore_run(chain, 1.0)]
    partial = {
        "ref": "d.jsonl",
        "source_session_ids": ["s1"],
        "redacted": True,
        "deduplicated": False,
        "held_out_split": False,
    }
    decision = hg.evaluate_promotion(chain, suite(), runs, dataset=partial)
    assert any("deduplicated" in r for r in decision.reasons)
    assert any("held-out" in r for r in decision.reasons)


def test_ignores_other_chains_and_stages(chain, run):
    from assistx import harness_chain as hc

    other_chain = hc.chain_from_run({**run, "endpoint": "optiplex:1234"})
    runs = [
        rescore_run(chain, 1.0),
        rescore_run(chain, 1.0),
        # Wrong chain.
        rescore_run(other_chain, 0.1),
        # Wrong stage.
        rescore_run(chain, 0.1, stage="reflect"),
    ]
    decision = hg.evaluate_promotion(chain, suite(), runs, dataset=good_dataset())
    assert decision.promote is True


def test_unregistered_suite_never_promotes(chain, run):
    from assistx.evaluation_registry import HarnessSuiteDef

    runs = [rescore_run(chain, 1.0), rescore_run(chain, 1.0)]
    custom = HarnessSuiteDef(id="not-in-registry.v9", schema_version="x")
    decision = hg.evaluate_promotion(chain, custom, runs, dataset=good_dataset())
    assert decision.promote is False
    assert decision.checks["suite_registered"] is False
