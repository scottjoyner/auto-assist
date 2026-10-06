"""Deterministic model task-fitness projection from subagent trace records.

Provider availability and model quality are intentionally separate. A 429/503,
quota, payment, auth, or control-plane failure is recorded as a provider failure
but does not reduce model-quality success rates. Router aliases and incomplete
model identities are excluded from model fitness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import statistics
from typing import Any, Iterable


PROVIDER_FAILURE_CLASSES = frozenset(
    {
        "rate_limited",
        "provider_overloaded",
        "quota_exhausted",
        "payment_required",
        "auth_error",
        "control_plane_timeout",
        "429",
        "503",
        "payment",
        "quota",
    }
)

NO_PROGRESS_STATUSES = frozenset(
    {
        "stalled",
        "stalled_abandoned",
        "no_progress",
        "aborted_no_progress",
    }
)


@dataclass(frozen=True)
class SubagentTraceRecord:
    provider: str
    requested_model: str
    effective_model: str | None
    model_identity_complete: bool
    route_kind: str
    terminal_status: str
    task_family: str
    failure_class: str | None = None
    last_finish_reason: str | None = None
    assistant_error_count: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    patch_parts: int = 0
    launch_head: str | None = None
    final_git_head: str | None = None
    dirty_path_evidence: bool = False
    token_counts: dict[str, int] = field(default_factory=dict)
    tests_passed: bool | None = None


def _is_exact_attributed(record: SubagentTraceRecord) -> bool:
    return (
        bool(record.model_identity_complete)
        and record.route_kind == "exact"
        and bool(record.provider)
        and bool(record.effective_model)
    )


def _token_proxy(record: SubagentTraceRecord) -> int:
    tokens = record.token_counts or {}
    return int(
        tokens.get("input", tokens.get("tokens", 0))
        + tokens.get("output", 0)
        + tokens.get("reasoning", 0)
        + tokens.get("cache_read", 0)
    )


def _has_progress(record: SubagentTraceRecord) -> bool:
    head_changed = bool(
        record.launch_head
        and record.final_git_head
        and record.launch_head != record.final_git_head
    )
    return bool(record.patch_parts > 0 or head_changed or record.dirty_path_evidence)


def classify_outcome(record: SubagentTraceRecord) -> str:
    """Classify one run for model-quality accounting."""
    if not _is_exact_attributed(record):
        return "excluded"

    if (record.failure_class or "") in PROVIDER_FAILURE_CLASSES:
        return "provider_failure"

    status = (record.terminal_status or "").lower()
    if status in NO_PROGRESS_STATUSES:
        return "no_progress"

    if status == "completed":
        return "success"

    if status in {"failed", "error", "aborted"}:
        return "model_failure"

    return "excluded"


def compute_fitness_record(record: SubagentTraceRecord) -> dict[str, Any]:
    outcome = classify_outcome(record)
    tools = sum((record.tool_counts or {}).values())
    return {
        "provider": record.provider,
        "requested_model": record.requested_model,
        "effective_model": record.effective_model,
        "model_identity_complete": record.model_identity_complete,
        "route_kind": record.route_kind,
        "terminal_status": record.terminal_status,
        "task_family": record.task_family,
        "outcome": outcome,
        "provider_failure": outcome == "provider_failure",
        "model_failed": outcome in {"model_failure", "no_progress"},
        "no_progress": outcome == "no_progress",
        "successful": outcome == "success",
        "tool_calls": tools,
        "patch_producing": _has_progress(record),
        "test_evidence": record.tests_passed is not None,
        "tests_passed": record.tests_passed,
        "token_proxy": _token_proxy(record),
    }


def _aggregate_group(records: list[SubagentTraceRecord]) -> dict[str, Any]:
    outcomes = [compute_fitness_record(record) for record in records]
    included = [row for row in outcomes if row["outcome"] != "excluded"]
    provider_failures = [row for row in included if row["outcome"] == "provider_failure"]
    quality = [row for row in included if row["outcome"] != "provider_failure"]

    # successful runs are those with outcome "success" that are not provider failures
    successful = [row for row in quality if row["outcome"] == "success"]
    no_progress = [row for row in quality if row["outcome"] == "no_progress"]
    model_failures = [row for row in quality if row["outcome"] == "model_failure"]
    patch_runs = [row for row in quality if row["patch_producing"]]
    test_runs = [row for row in quality if row["test_evidence"]]
    passing_test_runs = [row for row in test_runs if row["tests_passed"] is True]

    quality_count = len(quality)
    success_rate = len(successful) / quality_count if quality_count else 0.0
    no_progress_rate = len(no_progress) / quality_count if quality_count else 0.0
    model_failure_rate = len(model_failures) / quality_count if quality_count else 0.0
    patch_rate = len(patch_runs) / quality_count if quality_count else 0.0
    test_pass_rate = len(passing_test_runs) / len(test_runs) if test_runs else 0.0
    tool_use_rate = (
        sum(1 for row in quality if row["tool_calls"] > 0) / quality_count
        if quality_count
        else 0.0
    )

    # Provider failures are deliberately excluded from the model-quality
    # denominator. A bounded score rewards successful/progressive/tested work
    # and penalizes healthy-provider no-progress/model failures.
    score = (
        65.0 * success_rate
        + 15.0 * patch_rate
        + 10.0 * test_pass_rate
        + 10.0 * tool_use_rate
        - 35.0 * no_progress_rate
        - 20.0 * model_failure_rate
    )
    score = round(max(0.0, min(100.0, score)), 2)

    token_values = [row["token_proxy"] for row in quality]
    return {
        "completed_runs": sum(
            1 for record in records if record.terminal_status == "completed"
        ),
        "successful_runs": len(successful),
        "provider_failures": len(provider_failures),
        "model_failures": len(model_failures),
        "no_progress_runs": len(no_progress),
        "patch_producing_runs": len(patch_runs),
        "test_evidence_runs": len(test_runs),
        "passing_test_runs": len(passing_test_runs),
        "excluded_runs": len(outcomes) - len(included),
        "quality_scored_runs": quality_count,
        "success_rate": round(success_rate, 4),
        "no_progress_rate": round(no_progress_rate, 4),
        "model_failure_rate": round(model_failure_rate, 4),
        "patch_rate": round(patch_rate, 4),
        "test_pass_rate": round(test_pass_rate, 4),
        "mean_token_proxy": round(statistics.mean(token_values), 2)
        if token_values
        else 0.0,
        "median_token_proxy": round(statistics.median(token_values), 2)
        if token_values
        else 0.0,
        "fitness_score": score,
    }


def aggregate_fitness(records: Iterable[SubagentTraceRecord]) -> dict[str, Any]:
    """Aggregate a homogeneous record set."""
    return _aggregate_group(list(records))


def aggregate_by_model_task(
    records: Iterable[SubagentTraceRecord],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Group exact attributed runs by provider/effective model/task family."""
    grouped: dict[tuple[str, str, str], list[SubagentTraceRecord]] = {}
    for record in records:
        if not _is_exact_attributed(record):
            continue
        key = (record.provider, str(record.effective_model), record.task_family)
        grouped.setdefault(key, []).append(record)

    return {
        key: {
            "provider": key[0],
            "effective_model": key[1],
            "task_family": key[2],
            **_aggregate_group(group),
        }
        for key, group in sorted(grouped.items())
    }
