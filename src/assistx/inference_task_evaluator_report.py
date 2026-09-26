from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .inference_policy_experiment import (
    DEFAULT_AUTHORITY,
    canonical_sha256,
    load_cases,
)

TASK_EVAL_REPORT_SCHEMA = "assistx-task-evaluator-report-v1"


def load_task_evaluator_suite(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("task evaluator suite must be an object")
    suite_id = str(raw.get("suite_id") or "").strip()
    if not suite_id:
        raise ValueError("suite_id is required")
    required_case_ids = raw.get("required_case_ids")
    if not isinstance(required_case_ids, list) or not required_case_ids:
        raise ValueError("required_case_ids must be a non-empty list")
    ids = [str(value) for value in required_case_ids]
    if len(ids) != len(set(ids)):
        raise ValueError("required_case_ids must be unique")
    thresholds = raw.get("minimum_pass_rate_by_kind") or {}
    if not isinstance(thresholds, dict):
        raise ValueError("minimum_pass_rate_by_kind must be an object")
    normalized_thresholds: dict[str, float] = {}
    for kind, value in thresholds.items():
        rate = float(value)
        if not 0.0 <= rate <= 1.0:
            raise ValueError("task evaluator pass-rate thresholds must be 0..1")
        normalized_thresholds[str(kind)] = rate
    overall = float(raw.get("minimum_overall_pass_rate", 1.0))
    if not 0.0 <= overall <= 1.0:
        raise ValueError("minimum_overall_pass_rate must be 0..1")
    cases_file = str(raw.get("cases_file") or "").strip()
    if not cases_file:
        raise ValueError("task evaluator suite cases_file is required")
    cases = load_cases(cases_file)
    case_by_id = {
        str(case["case_id"]): case
        for case in cases
    }
    if set(case_by_id) != set(ids):
        raise ValueError(
            "task evaluator suite required_case_ids must exactly match "
            "the cases file"
        )
    for case in cases:
        if case.get("evaluation_suite") != suite_id:
            raise ValueError(
                f"{case['case_id']}: evaluation_suite must be {suite_id}"
            )
    case_sha256_by_id = {
        case_id: str(case_by_id[case_id]["case_sha256"])
        for case_id in sorted(case_by_id)
    }

    authority = raw.get("authority")
    if authority is not None:
        if not isinstance(authority, dict):
            raise ValueError("suite authority must be an object")
        for field in DEFAULT_AUTHORITY:
            if authority.get(field, False) is not False:
                raise ValueError(
                    f"suite authority.{field} must remain false"
                )

    normalized = {
        **raw,
        "schema": "assistx-task-evaluator-suite-v1",
        "suite_id": suite_id,
        "required_case_ids": ids,
        "minimum_pass_rate_by_kind": normalized_thresholds,
        "minimum_overall_pass_rate": overall,
        "cases_file": cases_file,
        "case_sha256_by_id": case_sha256_by_id,
        "cases_sha256": canonical_sha256(case_sha256_by_id),
    }
    normalized["suite_sha256"] = canonical_sha256({
        key: value
        for key, value in normalized.items()
        if key != "suite_sha256"
    })
    return normalized


def summarize_task_evaluator_results(
    rows: Iterable[dict[str, Any]],
    suite: dict[str, Any],
) -> dict[str, Any]:
    suite_id = str(suite["suite_id"])
    required_case_ids = set(suite["required_case_ids"])
    by_policy: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("evaluation_suite") != suite_id:
            continue
        policy_id = str(row.get("policy_id") or "")
        case_id = str(row.get("case_id") or "")
        if not policy_id or not case_id:
            continue
        policy_rows = by_policy.setdefault(policy_id, {})
        if case_id in policy_rows:
            raise ValueError(
                f"duplicate task-evaluator result for {policy_id}/{case_id}"
            )
        policy_rows[case_id] = row

    policies: list[dict[str, Any]] = []
    for policy_id, case_rows in sorted(by_policy.items()):
        present = set(case_rows)
        missing = sorted(required_case_ids - present)
        unexpected = sorted(present - required_case_ids)
        scored = [
            row for case_id, row in case_rows.items()
            if case_id in required_case_ids
        ]
        case_hash_mismatches = sorted(
            str(row.get("case_id"))
            for row in scored
            if row.get("case_sha256")
            != suite["case_sha256_by_id"].get(
                str(row.get("case_id") or "")
            )
        )
        failed = sorted(
            str(row.get("case_id"))
            for row in scored
            if (
                not _row_passed(row)
                or str(row.get("case_id")) in case_hash_mismatches
            )
        )
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for row in scored:
            kind = str(row.get("evaluator_kind") or "unknown")
            by_kind.setdefault(kind, []).append(row)

        kind_rates: dict[str, Any] = {}
        kind_gate_passed = True
        thresholds = suite["minimum_pass_rate_by_kind"]
        for kind, kind_rows in sorted(by_kind.items()):
            pass_count = sum(1 for row in kind_rows if _row_passed(row))
            rate = pass_count / len(kind_rows) if kind_rows else 0.0
            required = float(thresholds.get(kind, 1.0))
            passed = rate >= required
            kind_gate_passed = kind_gate_passed and passed
            kind_rates[kind] = {
                "cases": len(kind_rows),
                "passed_cases": pass_count,
                "pass_rate": round(rate, 9),
                "required_minimum": required,
                "passed": passed,
            }

        overall_pass_count = sum(1 for row in scored if _row_passed(row))
        overall_rate = (
            overall_pass_count / len(required_case_ids)
            if required_case_ids
            else 0.0
        )
        complete = not missing and not unexpected
        eligible = (
            complete
            and not case_hash_mismatches
            and overall_rate >= float(suite["minimum_overall_pass_rate"])
            and kind_gate_passed
        )
        exemplar = next(iter(case_rows.values()), {})
        policies.append(
            {
                "policy_id": policy_id,
                "policy_sha256": exemplar.get("policy_sha256"),
                "node_id": exemplar.get("node_id"),
                "model_handle": exemplar.get("model_handle"),
                "backend": exemplar.get("backend"),
                "quantization": exemplar.get("quantization"),
                "speculation": exemplar.get("speculation"),
                "required_case_count": len(required_case_ids),
                "observed_case_count": len(scored),
                "missing_case_ids": missing,
                "unexpected_case_ids": unexpected,
                "failed_case_ids": failed,
                "case_hash_mismatch_ids": case_hash_mismatches,
                "overall_pass_rate": round(overall_rate, 9),
                "required_overall_pass_rate": suite["minimum_overall_pass_rate"],
                "by_evaluator_kind": kind_rates,
                "eligible_for_training_evidence": eligible,
                "routing_authority_changed": False,
            }
        )

    return {
        "schema": TASK_EVAL_REPORT_SCHEMA,
        "suite_id": suite_id,
        "suite_sha256": suite["suite_sha256"],
        "cases_sha256": suite["cases_sha256"],
        "required_case_ids": sorted(required_case_ids),
        "policy_count": len(policies),
        "policies": policies,
        "production_promotion_authorized": False,
        "routing_authority_changed": False,
        "authority": dict(DEFAULT_AUTHORITY),
        "report_sha256": canonical_sha256({
            "suite_sha256": suite["suite_sha256"],
            "policies": policies,
        }),
        "note": (
            "Task-evaluator evidence is offline quality evidence only. "
            "It cannot authorize routing, runtime admission, tool use, "
            "claims, approvals, or mutation."
        ),
    }


def _row_passed(row: dict[str, Any]) -> bool:
    authority = row.get("authority")
    authority_safe = (
        isinstance(authority, dict)
        and all(
            authority.get(field, False) is False
            for field in DEFAULT_AUTHORITY
        )
    )
    return (
        row.get("success") is True
        and row.get("acceptance_passed") is True
        and row.get("execution_mode") == "observe_only"
        and row.get("allow_model_load") is False
        and row.get("routing_authority_changed") is False
        and authority_safe
        and (
            row.get("telemetry_required") is not True
            or row.get("telemetry_valid") is True
        )
    )
