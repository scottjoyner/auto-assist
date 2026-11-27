"""Round 50: allocation ranking must not credit zeroed or unusable evidence.

Hermetic: every case is plain dicts in / dicts out. No DB, service, socket,
credential, network mount or temporary file is used.
"""

from __future__ import annotations

import json
import math

import pytest

from assistx.allocation_engine import build_allocation_plan

_TASK = [{"id": "task-r50", "status": "READY", "priority": "HIGH"}]
_NODES = [
    {"hostname": "floor-failed", "online": True, "loaded_models": ["m"], "inflight_tasks": 0},
    {"hostname": "qualified", "online": True, "loaded_models": ["m"], "inflight_tasks": 0},
]


def _plan(entries: list[dict]) -> dict:
    return build_allocation_plan(_TASK, _NODES, {"entries": entries})


def _candidates(plan: dict) -> dict[str, dict]:
    recommendation = plan["recommendations"][0]
    return {
        row["node_id"]: row
        for row in [recommendation["recommended"], *recommendation["alternatives"]]
        if row is not None
    }


def _evidence(node_id: str, **overrides) -> dict:
    row = {
        "node_id": node_id,
        "model_id": "m",
        "quality_score": 0.9,
        "confidence": 0.9,
        "tokens_per_second": 50,
    }
    row.update(overrides)
    return row


# The benchmark policy deliberately zeroes quality_score/confidence when a
# quality floor fails (benchmark_allocation_policy.py:235-237). The allocator
# must not turn that fail-closed zero back into a mediocre credit.
def test_zeroed_quality_floor_evidence_gets_no_ranking_credit():
    plan = _plan([
        _evidence("floor-failed", quality_score=0.0, confidence=0.0),
        _evidence("qualified", quality_score=0.6, confidence=0.6, tokens_per_second=20),
    ])
    recommendation = plan["recommendations"][0]

    assert recommendation["recommended"]["node_id"] == "qualified"
    failed = _candidates(plan)["floor-failed"]
    assert failed["components"]["quality"] == 0.0
    assert failed["components"]["evidence_confidence"] == 0.0


@pytest.mark.parametrize(
    "bad_quality,bad_confidence",
    [
        ("inf", "nan"),
        ("nan", "inf"),
        (float("inf"), float("-inf")),
        ("not-a-number", ""),
    ],
)
def test_nonfinite_quality_and_confidence_cannot_poison_ranking(bad_quality, bad_confidence):
    plan = _plan([
        _evidence("floor-failed", quality_score=bad_quality, confidence=bad_confidence),
        _evidence("qualified"),
    ])
    recommendation = plan["recommendations"][0]

    assert recommendation["recommended"]["node_id"] == "qualified"
    for row in _candidates(plan).values():
        assert math.isfinite(row["score"]), row
        assert math.isfinite(row["components"]["quality"]), row
        assert math.isfinite(row["components"]["evidence_confidence"]), row
        assert math.isfinite(row["components"]["throughput"]), row
    # A NaN/Inf anywhere in the plan would emit invalid JSON downstream.
    json.dumps(plan, allow_nan=False)


@pytest.mark.parametrize(
    "bad_tps",
    ["nan", "inf", "not-a-number", True, float("nan")],
)
def test_unusable_throughput_cannot_claim_full_credit(bad_tps):
    plan = _plan([
        _evidence("floor-failed", quality_score=0.5, confidence=0.5, tokens_per_second=bad_tps),
        _evidence("qualified", quality_score=0.5, confidence=0.5, tokens_per_second=10),
    ])
    recommendation = plan["recommendations"][0]

    assert recommendation["recommended"]["node_id"] == "qualified"
    poisoned = _candidates(plan)["floor-failed"]
    assert poisoned["components"]["throughput"] == 0.0
    assert math.isfinite(poisoned["score"])


def test_malformed_evidence_never_aborts_the_plan():
    plan = _plan([
        _evidence("floor-failed", quality_score="oops", confidence=object(), tokens_per_second="oops"),
        _evidence("qualified"),
    ])

    assert plan["summary"] == {"ready_tasks": 1, "placeable": 1, "blocked": 0}
    assert plan["recommendations"][0]["recommended"]["node_id"] == "qualified"


# Controls: genuine evidence and genuinely absent keys keep their old meaning.
def test_genuine_evidence_is_unchanged():
    plan = _plan([
        _evidence("qualified", quality_score=0.9, confidence=0.8, tokens_per_second=25),
        _evidence("floor-failed", quality_score=0.7, confidence=0.7, tokens_per_second=10),
    ])
    top = plan["recommendations"][0]["recommended"]

    assert top["node_id"] == "qualified"
    assert top["score"] == 0.758
    assert top["components"]["quality"] == 0.9
    assert top["components"]["evidence_confidence"] == 0.8
    assert top["components"]["throughput"] == 0.5


def test_missing_evidence_keeps_historical_defaults():
    plan = _plan([{"node_id": "qualified", "model_id": "m"}])
    top = plan["recommendations"][0]["recommended"]

    assert top["score"] == 0.524
    assert top["components"]["quality"] == 0.5
    assert top["components"]["evidence_confidence"] == 0.25
    assert top["components"]["throughput"] == 0.0
