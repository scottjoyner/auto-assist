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
    post_hoc_measurement,
    speculation_evidence,
    task_spec_anchors,
    task_spec_group_id,
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
        "telemetry_env": policy_id.upper().replace("-", "_") + "_TELEMETRY_URL",
        "execution_mode": "observe_only",
        "allow_model_load": False,
        "enabled": True,
        "authority": dict(DEFAULT_AUTHORITY),
    }


def _fixture(tmp_path: Path, duplicates: bool = False):
    cases = [
        {
            "case_id": "case-a",
            "task_family": "coding",
            "prompt": "Return a safe implementation.",
            "acceptance": {
                "required_terms": ["safe"],
                "task_evaluator": {
                    "kind": "python_function",
                    "function_name": "double_all",
                    "tests": [{"args": [[1, 2]], "expected": [2, 4]}],
                },
            },
        },
        {
            "case_id": "case-b",
            "task_family": "tool_use",
            "prompt": "Return a read-only observation.",
            "acceptance": {
                "required_terms": ["read-only"],
                "task_evaluator": {
                    "kind": "python_function",
                    "function_name": "halve_all",
                    "tests": [{"args": [[2, 4]], "expected": [1, 2]}],
                },
            },
        },
    ]
    if duplicates:
        # Same task, different prompt: a naive split puts these two in
        # different splits and hands the model the held-out task in training.
        cases.append(
            {
                "case_id": "case-a2",
                "task_family": "coding",
                "prompt": "Return a safe implementation, phrased differently.",
                "acceptance": dict(cases[0]["acceptance"]),
            }
        )
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
    if duplicates:
        walls["case-a2"] = {
            "x1-none-32k": 110.0,
            "r9-none-32k": 108.0,
            "r9-df-32k": 66.0,
            "x1-none-128k": 214.0,
            "r9-none-128k": 166.0,
            "r9-df-128k": 126.0,
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
                    "ttft_ms": wall_ms * 0.4,
                    "decode_window_ms": wall_ms * 0.6,
                    "prompt_tokens": 20,
                    "completion_tokens": 40,
                    "output_chars": 160,
                    "tokens_per_second": 40.0,
                    "finish_reason": "stop",
                    "response_timings": {
                        "draft_n": 40,
                        "draft_n_accepted": 32,
                        "predicted_n": 40,
                        "predicted_ms": wall_ms * 0.6,
                        "prompt_ms": wall_ms * 0.4,
                    },
                    "runtime_telemetry": {
                        "counter_deltas": {
                            "spec_proposed_tokens": 40.0,
                            "spec_accepted_tokens": 32.0,
                            "spec_verification_steps": 8.0,
                        }
                    },
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


def test_duplicate_task_specs_never_straddle_splits(tmp_path):
    paths = _fixture(tmp_path, duplicates=True)
    bundle = _build(paths)

    splits_by_case = {}
    for row in bundle["records"]:
        splits_by_case.setdefault(row["metadata"]["case_id"], set()).add(row["split"])

    by_spec = {}
    for line in paths["cases"].read_text().splitlines():
        case = json.loads(line)
        spec = json.dumps(case["acceptance"]["task_evaluator"], sort_keys=True)
        by_spec.setdefault(spec, set()).update(splits_by_case[case["case_id"]])

    assert len(by_spec) == 2, "case-a and case-a2 share one spec, case-b another"
    for spec, splits in by_spec.items():
        assert len(splits) == 1, f"task spec leaked across splits: {sorted(splits)}"


def test_duplicate_specs_anchor_on_the_first_case_id():
    spec = {"kind": "python_function", "function_name": "f", "tests": []}
    cases = [
        {"case_id": "case-b", "acceptance": {"task_evaluator": spec}},
        {"case_id": "case-a", "acceptance": {"task_evaluator": spec}},
    ]
    anchors = task_spec_anchors(cases)

    assert len(anchors) == 1, "only duplicated specs need an anchor"
    assert task_spec_group_id(cases[1], anchors=anchors) == "case-a"
    assert task_spec_group_id(cases[0], anchors=anchors) == "case-a"


def test_distinct_specs_keep_their_own_group():
    cases = [
        {
            "case_id": case_id,
            "acceptance": {
                "task_evaluator": {"function_name": case_id, "tests": []},
            },
        }
        for case_id in ("case-a", "case-b")
    ]

    assert task_spec_anchors(cases) == {}
    for case in cases:
        assert task_spec_group_id(case, anchors={}) == case["case_id"]


def test_declared_group_beats_spec_anchoring():
    spec = {"kind": "python_function", "function_name": "f", "tests": []}
    cases = [
        {"case_id": "case-a", "acceptance": {"task_evaluator": spec}},
        {
            "case_id": "case-b",
            "dataset_group": "hand-written",
            "acceptance": {"task_evaluator": spec},
        },
    ]

    assert task_spec_group_id(cases[1], anchors=task_spec_anchors(cases)) == (
        "hand-written"
    )


def test_case_without_a_task_evaluator_is_its_own_group():
    case = {"case_id": "case-a", "acceptance": {"required_terms": ["safe"]}}

    assert task_spec_group_id(case) == "case-a"


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


def test_post_hoc_measurement_carries_the_prefill_decode_split():
    row = {
        "wall_ms": 1000.0,
        "ttft_ms": 400.0,
        "decode_window_ms": 600.0,
        "prompt_tokens": 50,
        "completion_tokens": 40,
        "output_chars": 160,
        "tokens_per_second": 66.7,
        "finish_reason": "stop",
    }

    measurement = post_hoc_measurement(row)

    assert measurement["prefill_share"] == pytest.approx(0.4)
    assert measurement["decode_share"] == pytest.approx(0.6)
    assert measurement["ms_per_completion_token"] == pytest.approx(25.0)
    assert measurement["completion_tokens"] == 40
    assert measurement["finish_reason"] == "stop"


def test_post_hoc_measurement_drops_absent_and_mistyped_fields():
    measurement = post_hoc_measurement(
        {
            "wall_ms": 10.0,
            "ttft_ms": None,
            "decode_window_ms": "fast",
            # a bool is an int in Python; it must not be recorded as a count
            "completion_tokens": True,
            "wall_ms_is_not_a_measured_field": 5,
        }
    )

    assert "ttft_ms" not in measurement
    assert "decode_window_ms" not in measurement
    assert "completion_tokens" not in measurement
    assert "prefill_share" not in measurement
    assert measurement["note"]


def test_post_hoc_measurement_is_labelled_as_unavailable_to_the_router():
    # The whole reason this is evidence and not a feature: a router must
    # choose before generating, so none of it exists at decision time.
    measurement = post_hoc_measurement({"wall_ms": 10.0, "ttft_ms": 4.0})

    assert "never available to the router" in measurement["note"]


def test_bundle_carries_post_hoc_evidence_without_leaking_it_into_state(tmp_path):
    bundle = _build(_fixture(tmp_path))

    record = bundle["records"][0]

    evidence = record["metadata"]["candidate_evidence"]
    assert evidence, "record has at least one candidate"
    for candidate in evidence.values():
        measurement = candidate["post_hoc_measurement"]
        assert measurement["completion_tokens"] == 40
        assert measurement["prefill_share"] == pytest.approx(0.4)

    state = json.dumps(record["state"])
    for leaked in ("ttft_ms", "decode_window_ms", "completion_tokens", "post_hoc"):
        assert leaked not in state, f"{leaked} must not reach the router input"


def test_speculation_evidence_reports_acceptance_and_steps_per_draft():
    evidence = speculation_evidence(
        {
            "response_timings": {"draft_n": 42, "draft_n_accepted": 31},
            "runtime_telemetry": {
                "counter_deltas": {"spec_verification_steps": 7.0}
            },
        }
    )

    assert evidence["draft_tokens_proposed"] == 42
    assert evidence["draft_tokens_accepted"] == 31
    assert evidence["spec_acceptance_rate"] == pytest.approx(31 / 42)
    assert evidence["verification_steps"] == 7.0
    # tokens per verification step is the quantity that decides throughput:
    # a lower acceptance rate can still win by proposing longer drafts
    assert evidence["draft_tokens_per_step"] == pytest.approx(6.0)
    assert evidence["accepted_tokens_per_step"] == pytest.approx(31 / 7)


def test_speculation_evidence_reads_draft_accounting_from_either_source():
    from_timings = speculation_evidence(
        {"response_timings": {"draft_n": 10, "draft_n_accepted": 5}}
    )
    from_telemetry = speculation_evidence(
        {
            "runtime_telemetry": {
                "counter_deltas": {
                    "spec_proposed_tokens": 10.0,
                    "spec_accepted_tokens": 5.0,
                }
            }
        }
    )

    # per-request draft counts only come from the endpoint's own timings
    assert from_timings["draft_tokens_proposed"] == 10
    assert from_timings["spec_acceptance_rate"] == pytest.approx(0.5)
    # the sidecar's counters are window totals, kept under their own names
    assert "draft_tokens_proposed" not in from_telemetry
    assert from_telemetry["window_proposed_tokens"] == 10.0
    assert from_telemetry["window_accepted_tokens"] == 5.0


def test_speculation_evidence_is_empty_without_draft_accounting():
    # A non-speculative trial has no draft accounting, and that must not be
    # reported as a zero acceptance rate.
    assert speculation_evidence({"wall_ms": 10.0}) == {}
    assert speculation_evidence({"response_timings": {}}) == {}
    assert speculation_evidence({"runtime_telemetry": None}) == {}
    zero = speculation_evidence({"response_timings": {"draft_n": 0}})
    assert "spec_acceptance_rate" not in zero


def test_bundle_carries_speculation_evidence(tmp_path):
    bundle = _build(_fixture(tmp_path))

    for record in bundle["records"]:
        for candidate in record["metadata"]["candidate_evidence"].values():
            speculation = candidate["post_hoc_measurement"]["speculation"]
            assert speculation["draft_tokens_proposed"] == 40
            assert speculation["spec_acceptance_rate"] == pytest.approx(0.8)
            assert speculation["draft_tokens_per_step"] == pytest.approx(5.0)
