from __future__ import annotations

import json
from pathlib import Path

import pytest

from assistx.inference_policy_experiment import (
    DEFAULT_AUTHORITY,
    canonical_sha256,
    load_matrix,
)
from assistx.inference_policy_training_dataset import (
    assign_group_splits,
    build_policy_training_bundle,
)


def _write_json(path: Path, value):
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _policy(policy_id, context_tokens, node, backend, speculation):
    return {
        "policy_id": policy_id,
        "node_id": node,
        "model_handle": "qwen3.8-27b-q4",
        "backend": backend,
        "quantization": "q4",
        "speculation": speculation,
        "context_tokens": context_tokens,
        "concurrency": 1,
        "endpoint_env": policy_id.upper().replace("-", "_") + "_URL",
        "telemetry_required": True,
        "execution_mode": "observe_only",
        "allow_model_load": False,
        "enabled": True,
        "authority": dict(DEFAULT_AUTHORITY),
    }


def _fixture(tmp_path: Path):
    cases = [
        {
            "case_id": "case-a",
            "task_family": "coding",
            "prompt": "Return a safe implementation.",
            "acceptance": {"required_terms": ["safe"]},
        },
        {
            "case_id": "case-b",
            "task_family": "tool_use",
            "prompt": "Return a read-only observation.",
            "acceptance": {"required_terms": ["read-only"]},
        },
    ]
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(cases_path, cases)

    policies = [
        _policy("x1-none-32k", 32768, "x1-370", "vulkan", "none"),
        _policy("r9-none-32k", 32768, "r9700", "rocm", "none"),
        _policy("r9-df-32k", 32768, "r9700", "rocm", "dflash"),
        _policy("x1-none-128k", 131072, "x1-370", "vulkan", "none"),
        _policy("r9-none-128k", 131072, "r9700", "rocm", "none"),
        _policy("r9-df-128k", 131072, "r9700", "rocm", "dflash"),
    ]
    matrix_path = tmp_path / "matrix.json"
    _write_json(
        matrix_path,
        {"schema": "assistx-inference-policy-matrix-v1", "policies": policies},
    )
    matrix = load_matrix(matrix_path)
    by_id = {row["policy_id"]: row for row in matrix["policies"]}

    source_quality = {
        "schema": "assistx-task-evaluator-report-v1",
        "policies": [
            {
                "policy_id": "x1-none-32k",
                "policy_sha256": by_id["x1-none-32k"]["policy_sha256"],
                "eligible_for_training_evidence": True,
            },
            {
                "policy_id": "r9-none-32k",
                "policy_sha256": by_id["r9-none-32k"]["policy_sha256"],
                "eligible_for_training_evidence": True,
            },
            {
                "policy_id": "r9-df-32k",
                "policy_sha256": by_id["r9-df-32k"]["policy_sha256"],
                "eligible_for_training_evidence": False,
            },
        ],
        "authority": dict(DEFAULT_AUTHORITY),
        "production_promotion_authorized": False,
        "routing_authority_changed": False,
    }
    target_quality = {
        "schema": "assistx-task-evaluator-report-v1",
        "policies": [
            {
                "policy_id": policy_id,
                "policy_sha256": by_id[policy_id]["policy_sha256"],
                "eligible_for_training_evidence": True,
            }
            for policy_id in (
                "x1-none-128k",
                "r9-none-128k",
                "r9-df-128k",
            )
        ],
        "authority": dict(DEFAULT_AUTHORITY),
        "production_promotion_authorized": False,
        "routing_authority_changed": False,
    }
    source_quality_path = tmp_path / "source-quality.json"
    target_quality_path = tmp_path / "target-quality.json"
    _write_json(source_quality_path, source_quality)
    _write_json(target_quality_path, target_quality)
    source_quality_sha = canonical_sha256(source_quality)
    target_quality_sha = canonical_sha256(target_quality)

    source_campaign = {
        "schema": "assistx-inference-soak-campaign-evidence-v1",
        "evaluated_candidates": [
            {
                "source_policy_id": policy_id,
                "eligible_for_target_context_experiment": True,
            }
            for policy_id in (
                "x1-none-32k",
                "r9-none-32k",
                "r9-df-32k",
            )
        ],
        "task_quality_report_sha256": source_quality_sha,
        "authority": dict(DEFAULT_AUTHORITY),
        "production_promotion_authorized": False,
        "routing_authority_changed": False,
    }
    source_campaign_path = tmp_path / "source-campaign.json"
    _write_json(source_campaign_path, source_campaign)
    source_campaign_sha = canonical_sha256(source_campaign)

    target_campaign = {
        "schema": "assistx-inference-soak-campaign-target-evidence-v1",
        "source_evidence_sha256": source_campaign_sha,
        "task_quality_report_sha256": target_quality_sha,
        "benchmark_complete_target_context_policies": [
            {"policy_id": policy_id}
            for policy_id in (
                "x1-none-128k",
                "r9-none-128k",
                "r9-df-128k",
            )
        ],
        "authority": dict(DEFAULT_AUTHORITY),
        "production_promotion_authorized": False,
        "routing_authority_changed": False,
    }
    target_campaign_path = tmp_path / "target-campaign.json"
    _write_json(target_campaign_path, target_campaign)

    case_hashes = {}
    from assistx.inference_policy_experiment import load_cases

    for case in load_cases(cases_path):
        case_hashes[case["case_id"]] = case["case_sha256"]

    rows = []
    walls = {
        "case-a": {
            "x1-none-32k": 100.0,
            "r9-none-32k": 102.0,
            "r9-df-32k": 60.0,
            "x1-none-128k": 210.0,
            "r9-none-128k": 160.0,
            "r9-df-128k": 120.0,
        },
        "case-b": {
            "x1-none-32k": 140.0,
            "r9-none-32k": 100.0,
            "r9-df-32k": 70.0,
            "x1-none-128k": 220.0,
            "r9-none-128k": 170.0,
            "r9-df-128k": 130.0,
        },
    }
    for case_id, per_policy in walls.items():
        for policy_id, wall_ms in per_policy.items():
            policy = by_id[policy_id]
            rows.append(
                {
                    "schema": "assistx-inference-policy-result-v1",
                    "case_id": case_id,
                    "case_sha256": case_hashes[case_id],
                    "policy_id": policy_id,
                    "policy_sha256": policy["policy_sha256"],
                    "context_tokens": policy["context_tokens"],
                    "success": True,
                    "acceptance_passed": True,
                    "execution_mode": "observe_only",
                    "allow_model_load": False,
                    "routing_authority_changed": False,
                    "authority": dict(DEFAULT_AUTHORITY),
                    "telemetry_required": True,
                    "telemetry_valid": True,
                    "wall_ms": wall_ms,
                }
            )
    results_path = tmp_path / "results.jsonl"
    _write_jsonl(results_path, rows)
    return {
        "cases": cases_path,
        "matrix": matrix_path,
        "results": [results_path],
        "source_campaign": source_campaign_path,
        "target_campaign": target_campaign_path,
        "source_quality": source_quality_path,
        "target_quality": target_quality_path,
    }


def _build(paths):
    return build_policy_training_bundle(
        cases_file=paths["cases"],
        matrix_file=paths["matrix"],
        result_files=paths["results"],
        source_campaign_evidence_file=paths["source_campaign"],
        target_campaign_evidence_file=paths["target_campaign"],
        source_quality_report_file=paths["source_quality"],
        target_quality_report_file=paths["target_quality"],
        producer_git_sha="5caad17f0ab2e83194f944e953db3f9397e17dbd",
    )


def test_group_split_is_deterministic_and_four_way_for_eight_groups():
    groups = [f"group-{index}" for index in range(8)]
    first = assign_group_splits(groups, seed="fixed")
    second = assign_group_splits(reversed(groups), seed="fixed")

    assert first == second
    counts = {
        split: sum(1 for value in first.values() if value == split)
        for split in ("train", "validation", "calibration", "test")
    }
    assert counts == {
        "train": 5,
        "validation": 1,
        "calibration": 1,
        "test": 1,
    }


def test_bundle_uses_only_quality_and_campaign_admissible_policies(tmp_path):
    bundle = _build(_fixture(tmp_path))

    assert bundle["manifest"]["record_count"] == 4
    assert bundle["manifest"]["evidence_only"] is True
    assert all(
        value is False
        for value in bundle["manifest"]["authority"].values()
    )
    for record in bundle["records"]:
        options = record["questions"]["execution_policy"]["options"]
        assert len(options) >= 2
        policy_ids = {
            evidence["policy_id"]
            for evidence in record["metadata"]["candidate_evidence"].values()
        }
        if record["metadata"]["context_tokens"] == 32768:
            assert "r9-df-32k" not in policy_ids


def test_near_tie_uses_distribution_instead_of_noisy_hard_label(tmp_path):
    bundle = _build(_fixture(tmp_path))
    record = next(
        row
        for row in bundle["records"]
        if row["metadata"]["case_id"] == "case-a"
        and row["metadata"]["context_tokens"] == 32768
    )

    distribution = record["targets"]["execution_policy"]["distribution"]
    assert distribution == [0.5, 0.5]
    assert len(record["metadata"]["near_best_signature_ids"]) == 2


def test_same_request_group_never_leaks_across_context_splits(tmp_path):
    bundle = _build(_fixture(tmp_path))
    by_case = {}
    for row in bundle["records"]:
        by_case.setdefault(row["metadata"]["case_id"], set()).add(row["split"])

    assert all(len(splits) == 1 for splits in by_case.values())


def test_target_campaign_must_bind_exact_source_evidence(tmp_path):
    paths = _fixture(tmp_path)
    target = json.loads(paths["target_campaign"].read_text(encoding="utf-8"))
    target["source_evidence_sha256"] = "0" * 64
    _write_json(paths["target_campaign"], target)

    with pytest.raises(ValueError, match="source evidence"):
        _build(paths)


def test_widened_quality_authority_is_rejected(tmp_path):
    paths = _fixture(tmp_path)
    source_quality = json.loads(
        paths["source_quality"].read_text(encoding="utf-8")
    )
    source_quality["authority"]["mutation_allowed"] = True
    _write_json(paths["source_quality"], source_quality)

    source_campaign = json.loads(
        paths["source_campaign"].read_text(encoding="utf-8")
    )
    source_campaign["task_quality_report_sha256"] = canonical_sha256(
        source_quality
    )
    _write_json(paths["source_campaign"], source_campaign)

    target_campaign = json.loads(
        paths["target_campaign"].read_text(encoding="utf-8")
    )
    target_campaign["source_evidence_sha256"] = canonical_sha256(
        source_campaign
    )
    _write_json(paths["target_campaign"], target_campaign)

    with pytest.raises(ValueError, match="authority"):
        _build(paths)
