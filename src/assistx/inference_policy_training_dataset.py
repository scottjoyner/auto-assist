from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .inference_policy_experiment import (
    DEFAULT_AUTHORITY,
    canonical_sha256,
    load_cases,
    load_matrix,
)

BUNDLE_SCHEMA = "assistx-policy-training-bundle-v1"
RECORD_SCHEMA = "assistx-policy-decision-record-v1"
SPLIT_SCHEMA = "sha256-ranked-group-v1"
DEFAULT_SPLIT_FRACTIONS = {
    "train": 0.625,
    "validation": 0.125,
    "calibration": 0.125,
    "test": 0.125,
}


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def _read_jsonl(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                line = raw.strip()
                if not line:
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(
                        f"{path}:{line_number}: expected JSON object"
                    )
                rows.append(value)
    return rows


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def policy_signature(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": str(policy["node_id"]),
        "model_handle": str(policy["model_handle"]),
        "backend": str(policy["backend"]),
        "quantization": str(policy["quantization"]),
        "speculation": str(policy["speculation"]),
        "concurrency": int(policy["concurrency"]),
    }


def policy_signature_id(policy: dict[str, Any]) -> str:
    return "exec-" + canonical_sha256(policy_signature(policy))[:16]


def _authority_is_safe(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and all(
            value.get(field, False) is False
            for field in DEFAULT_AUTHORITY
        )
    )


def _validate_evidence_boundary(value: dict[str, Any], name: str) -> None:
    if value.get("production_promotion_authorized") is not False:
        raise ValueError(f"{name}: production promotion must remain false")
    if value.get("routing_authority_changed") is not False:
        raise ValueError(f"{name}: routing authority must remain unchanged")
    if not _authority_is_safe(value.get("authority")):
        raise ValueError(f"{name}: authority must remain all-false")


def _quality_policy_index(report: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    _validate_evidence_boundary(report, name)
    policies = report.get("policies")
    if not isinstance(policies, list):
        raise ValueError(f"{name}: policies must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for row in policies:
        if not isinstance(row, dict):
            continue
        policy_id = str(row.get("policy_id") or "")
        if not policy_id:
            continue
        if policy_id in indexed:
            raise ValueError(f"{name}: duplicate policy evidence {policy_id}")
        indexed[policy_id] = row
    return indexed


def _source_admissible_ids(evidence: dict[str, Any]) -> set[str]:
    _validate_evidence_boundary(evidence, "source_campaign_evidence")
    rows = evidence.get("evaluated_candidates")
    if not isinstance(rows, list):
        raise ValueError("source campaign evidence has no evaluated_candidates")
    return {
        str(row.get("source_policy_id"))
        for row in rows
        if isinstance(row, dict)
        and row.get("eligible_for_target_context_experiment") is True
        and row.get("source_policy_id")
    }


def _target_admissible_ids(evidence: dict[str, Any]) -> set[str]:
    _validate_evidence_boundary(evidence, "target_campaign_evidence")
    rows = evidence.get("benchmark_complete_target_context_policies")
    if not isinstance(rows, list):
        raise ValueError(
            "target campaign evidence has no benchmark-complete policy list"
        )
    return {
        str(row.get("policy_id"))
        for row in rows
        if isinstance(row, dict) and row.get("policy_id")
    }


def _validate_result_row(
    row: dict[str, Any],
    *,
    policy: dict[str, Any],
    case_sha256: str,
) -> tuple[bool, str | None]:
    if row.get("case_sha256") != case_sha256:
        return False, "case_sha256_mismatch"
    if row.get("policy_sha256") != policy.get("policy_sha256"):
        return False, "policy_sha256_mismatch"
    if row.get("success") is not True:
        return False, "request_failed"
    if row.get("acceptance_passed") is not True:
        return False, "acceptance_failed"
    if row.get("execution_mode") != "observe_only":
        return False, "execution_mode_not_observe_only"
    if row.get("allow_model_load") is not False:
        return False, "allow_model_load_not_false"
    if row.get("routing_authority_changed") is not False:
        return False, "routing_authority_changed"
    if not _authority_is_safe(row.get("authority")):
        return False, "authority_not_all_false"
    if (
        row.get("telemetry_required") is True
        and row.get("telemetry_valid") is not True
    ):
        return False, "telemetry_invalid"
    wall_ms = row.get("wall_ms")
    if not isinstance(wall_ms, (int, float)) or float(wall_ms) <= 0:
        return False, "wall_ms_invalid"
    return True, None


def _state_text(case: dict[str, Any], context_tokens: int) -> str:
    payload = {
        "task_family": case["task_family"],
        "context_tokens": int(context_tokens),
        "messages": case["messages"],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _split_counts(group_count: int) -> dict[str, int]:
    if group_count <= 0:
        return {name: 0 for name in DEFAULT_SPLIT_FRACTIONS}
    names = list(DEFAULT_SPLIT_FRACTIONS)
    raw = {
        name: group_count * DEFAULT_SPLIT_FRACTIONS[name]
        for name in names
    }
    counts = {name: int(raw[name]) for name in names}
    remaining = group_count - sum(counts.values())
    order = sorted(
        names,
        key=lambda name: (-(raw[name] - counts[name]), names.index(name)),
    )
    for name in order[:remaining]:
        counts[name] += 1

    nonzero = [name for name in names if DEFAULT_SPLIT_FRACTIONS[name] > 0]
    if group_count >= len(nonzero):
        for name in nonzero:
            if counts[name] > 0:
                continue
            donor = max(
                names,
                key=lambda candidate: (
                    counts[candidate],
                    DEFAULT_SPLIT_FRACTIONS[candidate],
                ),
            )
            if counts[donor] <= 1:
                raise ValueError("cannot create non-empty deterministic splits")
            counts[donor] -= 1
            counts[name] += 1
    return counts


def assign_group_splits(
    group_ids: Iterable[str],
    *,
    seed: str,
) -> dict[str, str]:
    groups = sorted(set(str(value) for value in group_ids))
    ranked = sorted(
        groups,
        key=lambda value: hashlib.sha256(
            (seed + "\0" + value).encode("utf-8")
        ).hexdigest(),
    )
    counts = _split_counts(len(ranked))
    result: dict[str, str] = {}
    offset = 0
    for split in ("train", "validation", "calibration", "test"):
        count = counts[split]
        for group in ranked[offset : offset + count]:
            result[group] = split
        offset += count
    if len(result) != len(ranked):
        raise AssertionError("split assignment did not cover every group")
    return result


def _record_for_group(
    *,
    case: dict[str, Any],
    context_tokens: int,
    rows: list[dict[str, Any]],
    policies: dict[str, dict[str, Any]],
    admissible_ids: set[str],
    quality: dict[str, dict[str, Any]],
    campaign_evidence_sha256: str,
    quality_report_sha256: str,
    tie_ratio: float,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        policy_id = str(row.get("policy_id") or "")
        policy = policies.get(policy_id)
        if policy is None or policy_id not in admissible_ids:
            continue
        quality_row = quality.get(policy_id)
        if (
            not isinstance(quality_row, dict)
            or quality_row.get("eligible_for_training_evidence") is not True
            or quality_row.get("policy_sha256") != policy.get("policy_sha256")
        ):
            continue
        valid, _ = _validate_result_row(
            row,
            policy=policy,
            case_sha256=str(case["case_sha256"]),
        )
        if not valid:
            continue
        candidates.append(
            {
                "policy_id": policy_id,
                "signature_id": policy_signature_id(policy),
                "descriptor": policy_signature(policy),
                "wall_ms": float(row["wall_ms"]),
                "result_sha256": canonical_sha256(row),
            }
        )

    by_signature: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        signature_id = candidate["signature_id"]
        previous = by_signature.get(signature_id)
        if previous is None or candidate["wall_ms"] < previous["wall_ms"]:
            by_signature[signature_id] = candidate
    options = sorted(by_signature)
    if len(options) < 2:
        return None

    best_wall = min(by_signature[option]["wall_ms"] for option in options)
    near_best = {
        option
        for option in options
        if by_signature[option]["wall_ms"] <= best_wall * tie_ratio
    }
    distribution = [
        (1.0 / len(near_best)) if option in near_best else 0.0
        for option in options
    ]
    group_id = str(
        case.get("dataset_group")
        or case.get("source_group_id")
        or case["case_id"]
    )
    record = {
        "schema": RECORD_SCHEMA,
        "record_id": "assistx-policy-" + canonical_sha256(
            {
                "case_sha256": case["case_sha256"],
                "context_tokens": int(context_tokens),
                "options": options,
                "distribution": distribution,
            }
        )[:24],
        "group_id": group_id,
        "state": _state_text(case, context_tokens),
        "questions": {
            "execution_policy": {
                "type": "choice",
                "instructions": (
                    "Choose the observed execution policy that preserves "
                    "AssistX acceptance and authority constraints while "
                    "minimizing request latency."
                ),
                "options": options,
                "metadata": {
                    "descriptors": {
                        option: by_signature[option]["descriptor"]
                        for option in options
                    }
                },
            }
        },
        "targets": {
            "execution_policy": {
                "distribution": distribution,
            }
        },
        "metadata": {
            "evidence_only": True,
            "dispatch_allowed": False,
            "routing_authority_changed": False,
            "case_id": case["case_id"],
            "case_sha256": case["case_sha256"],
            "task_family": case["task_family"],
            "context_tokens": int(context_tokens),
            "best_wall_ms": best_wall,
            "tie_ratio": tie_ratio,
            "near_best_signature_ids": sorted(near_best),
            "candidate_evidence": {
                option: {
                    "policy_id": by_signature[option]["policy_id"],
                    "wall_ms": by_signature[option]["wall_ms"],
                    "result_sha256": by_signature[option]["result_sha256"],
                }
                for option in options
            },
            "campaign_evidence_sha256": campaign_evidence_sha256,
            "quality_report_sha256": quality_report_sha256,
            "authority": dict(DEFAULT_AUTHORITY),
        },
    }
    record["record_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in record.items()
            if key != "record_sha256"
        }
    )
    return record


def build_policy_training_bundle(
    *,
    cases_file: str | Path,
    matrix_file: str | Path,
    result_files: list[str | Path],
    source_campaign_evidence_file: str | Path,
    target_campaign_evidence_file: str | Path,
    source_quality_report_file: str | Path,
    target_quality_report_file: str | Path,
    producer_git_sha: str,
    split_seed: str = "assistx-policy-training-v1",
    tie_ratio: float = 1.03,
) -> dict[str, Any]:
    if not producer_git_sha or len(producer_git_sha) < 12:
        raise ValueError("producer_git_sha is required")
    if tie_ratio < 1.0:
        raise ValueError("tie_ratio must be >= 1.0")

    cases = load_cases(cases_file)
    matrix = load_matrix(matrix_file)
    rows = _read_jsonl(result_files)
    source_campaign = _read_json(source_campaign_evidence_file)
    target_campaign = _read_json(target_campaign_evidence_file)
    source_quality = _read_json(source_quality_report_file)
    target_quality = _read_json(target_quality_report_file)

    source_campaign_sha = canonical_sha256(source_campaign)
    target_campaign_sha = canonical_sha256(target_campaign)
    source_quality_sha = canonical_sha256(source_quality)
    target_quality_sha = canonical_sha256(target_quality)

    if source_campaign.get("task_quality_report_sha256") != source_quality_sha:
        raise ValueError("source campaign does not bind the supplied quality report")
    if target_campaign.get("task_quality_report_sha256") != target_quality_sha:
        raise ValueError("target campaign does not bind the supplied quality report")
    if target_campaign.get("source_evidence_sha256") != source_campaign_sha:
        raise ValueError("target campaign does not bind the supplied source evidence")

    source_admissible = _source_admissible_ids(source_campaign)
    target_admissible = _target_admissible_ids(target_campaign)
    source_quality_index = _quality_policy_index(
        source_quality,
        "source_quality_report",
    )
    target_quality_index = _quality_policy_index(
        target_quality,
        "target_quality_report",
    )

    policies = {
        str(policy["policy_id"]): policy
        for policy in matrix["policies"]
        if policy.get("enabled", True)
    }
    cases_by_id = {str(case["case_id"]): case for case in cases}

    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    exclusions: dict[str, int] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "")
        policy_id = str(row.get("policy_id") or "")
        case = cases_by_id.get(case_id)
        policy = policies.get(policy_id)
        if case is None:
            exclusions["unknown_case"] = exclusions.get("unknown_case", 0) + 1
            continue
        if policy is None:
            exclusions["unknown_policy"] = exclusions.get("unknown_policy", 0) + 1
            continue
        context_tokens = int(policy["context_tokens"])
        grouped.setdefault((case_id, context_tokens), []).append(row)

    records: list[dict[str, Any]] = []
    contexts = sorted({int(policy["context_tokens"]) for policy in policies.values()})
    if len(contexts) < 2:
        raise ValueError("training bundle requires source and target contexts")
    source_context = min(contexts)
    target_context = max(contexts)

    for (case_id, context_tokens), group_rows in sorted(grouped.items()):
        if context_tokens == source_context:
            admissible = source_admissible
            quality = source_quality_index
            campaign_sha = source_campaign_sha
            quality_sha = source_quality_sha
        elif context_tokens == target_context:
            admissible = target_admissible
            quality = target_quality_index
            campaign_sha = target_campaign_sha
            quality_sha = target_quality_sha
        else:
            exclusions["unsupported_context"] = (
                exclusions.get("unsupported_context", 0) + len(group_rows)
            )
            continue
        record = _record_for_group(
            case=cases_by_id[case_id],
            context_tokens=context_tokens,
            rows=group_rows,
            policies=policies,
            admissible_ids=admissible,
            quality=quality,
            campaign_evidence_sha256=campaign_sha,
            quality_report_sha256=quality_sha,
            tie_ratio=tie_ratio,
        )
        if record is None:
            exclusions["insufficient_admissible_options"] = (
                exclusions.get("insufficient_admissible_options", 0) + 1
            )
            continue
        records.append(record)

    split_by_group = assign_group_splits(
        [str(record["group_id"]) for record in records],
        seed=split_seed,
    )
    for record in records:
        record["split"] = split_by_group[str(record["group_id"])]

    records.sort(key=lambda row: (str(row["split"]), str(row["record_id"])))
    split_counts = {
        split: sum(1 for row in records if row["split"] == split)
        for split in DEFAULT_SPLIT_FRACTIONS
    }

    manifest = {
        "schema": BUNDLE_SCHEMA,
        "producer": {
            "repository": "scottjoyner/auto-assist",
            "git_sha": producer_git_sha,
        },
        "record_schema": RECORD_SCHEMA,
        "split": {
            "schema": SPLIT_SCHEMA,
            "seed": split_seed,
            "fractions": dict(DEFAULT_SPLIT_FRACTIONS),
            "group_count": len(split_by_group),
            "record_counts": split_counts,
            "group_assignments": dict(sorted(split_by_group.items())),
        },
        "inputs": {
            "cases": {
                "path": str(cases_file),
                "sha256": _file_sha256(cases_file),
            },
            "matrix": {
                "path": str(matrix_file),
                "sha256": _file_sha256(matrix_file),
            },
            "result_files": [
                {
                    "path": str(path),
                    "sha256": _file_sha256(path),
                }
                for path in result_files
            ],
            "source_campaign_evidence_sha256": source_campaign_sha,
            "target_campaign_evidence_sha256": target_campaign_sha,
            "source_quality_report_sha256": source_quality_sha,
            "target_quality_report_sha256": target_quality_sha,
        },
        "tie_ratio": tie_ratio,
        "record_count": len(records),
        "excluded": dict(sorted(exclusions.items())),
        "records_sha256": canonical_sha256(
            [row["record_sha256"] for row in records]
        ),
        "authority": dict(DEFAULT_AUTHORITY),
        "evidence_only": True,
        "dispatch_allowed": False,
        "production_promotion_authorized": False,
        "routing_authority_changed": False,
    }
    manifest["bundle_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "bundle_sha256"
        }
    )
    return {
        "manifest": manifest,
        "records": records,
    }
