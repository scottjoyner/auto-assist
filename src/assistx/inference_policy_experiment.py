from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests

from .inference_task_evaluators import (
    evaluate_task_output,
    validate_task_evaluator_spec,
)

AUTHORITY_FIELDS = (
    "dispatch_allowed",
    "approval_granted",
    "claim_acquired",
    "mutation_allowed",
    "routing_authority_changed",
)

DEFAULT_AUTHORITY = {field: False for field in AUTHORITY_FIELDS}

_FAMILY_ALIASES = {
    "code": "coding",
    "code_review": "coding",
    "coding": "coding",
    "tool": "tool_use",
    "tools": "tool_use",
    "tool_use": "tool_use",
    "reasoning": "reasoning",
    "analysis": "reasoning",
    "research": "reasoning",
    "summary": "summarization",
    "summarize": "summarization",
    "summarization": "summarization",
    "extract": "extraction",
    "extraction": "extraction",
    "context": "long_context",
    "long_context": "long_context",
}


@dataclass(frozen=True)
class Trial:
    trial_id: str
    case: dict[str, Any]
    policy: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "case": self.case,
            "policy": self.policy,
        }


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"{path}:{line_number}: expected a JSON object"
                )
            rows.append(value)
    return rows


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    rows = _read_jsonl(path)
    return validate_cases(rows)


def load_shadow_cases(path: str | Path) -> list[dict[str, Any]]:
    return validate_cases(
        shadow_rows_to_cases(_read_jsonl(path))
    )


def load_matrix(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("matrix must be a JSON object")
    policies = value.get("policies")
    if not isinstance(policies, list) or not policies:
        raise ValueError("matrix.policies must be a non-empty list")
    validated = [validate_policy(policy) for policy in policies]
    ids = [policy["policy_id"] for policy in validated]
    if len(ids) != len(set(ids)):
        raise ValueError("policy_id values must be unique")
    return {
        **value,
        "schema": str(value.get("schema") or "assistx-inference-policy-matrix-v1"),
        "policies": validated,
    }


def validate_cases(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"case {index}: expected object")
        case_id = str(raw.get("case_id") or "").strip()
        if not case_id:
            raise ValueError(f"case {index}: case_id is required")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)

        messages = raw.get("messages")
        prompt = raw.get("prompt")
        if messages is None:
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(
                    f"{case_id}: messages or prompt is required"
                )
            messages = [{"role": "user", "content": prompt}]
        if (
            not isinstance(messages, list)
            or not messages
            or not all(isinstance(item, dict) for item in messages)
        ):
            raise ValueError(f"{case_id}: messages must be a non-empty list")

        family = _normalize_family(
            raw.get("task_family")
            or raw.get("classification")
            or "general"
        )
        acceptance = raw.get("acceptance")
        if acceptance is None:
            acceptance = {}
        if not isinstance(acceptance, dict):
            raise ValueError(f"{case_id}: acceptance must be an object")
        task_evaluator = acceptance.get("task_evaluator")
        if task_evaluator is not None:
            acceptance = {
                **acceptance,
                "task_evaluator": validate_task_evaluator_spec(
                    task_evaluator
                ),
            }

        result.append(
            {
                **raw,
                "case_id": case_id,
                "task_family": family,
                "messages": messages,
                "acceptance": acceptance,
                "case_sha256": canonical_sha256(
                    {
                        "case_id": case_id,
                        "task_family": family,
                        "messages": messages,
                        "acceptance": acceptance,
                    }
                ),
            }
        )
    return result


def validate_policy(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("policy must be an object")
    required = (
        "policy_id",
        "node_id",
        "model_handle",
        "backend",
        "quantization",
        "speculation",
        "context_tokens",
        "concurrency",
        "endpoint_env",
    )
    missing = [name for name in required if raw.get(name) in (None, "")]
    if missing:
        raise ValueError(
            "policy missing required fields: " + ", ".join(missing)
        )

    if raw.get("allow_model_load", False) is not False:
        raise ValueError(
            f"{raw.get('policy_id')}: allow_model_load must be false"
        )
    if str(raw.get("execution_mode") or "observe_only") != "observe_only":
        raise ValueError(
            f"{raw.get('policy_id')}: execution_mode must be observe_only"
        )

    authority = raw.get("authority") or DEFAULT_AUTHORITY
    if not isinstance(authority, dict):
        raise ValueError(f"{raw.get('policy_id')}: authority must be an object")
    for field in AUTHORITY_FIELDS:
        if authority.get(field, False) is not False:
            raise ValueError(
                f"{raw.get('policy_id')}: authority.{field} must be false"
            )

    context_tokens = int(raw["context_tokens"])
    concurrency = int(raw["concurrency"])
    if context_tokens <= 0:
        raise ValueError(
            f"{raw.get('policy_id')}: context_tokens must be positive"
        )
    if concurrency <= 0:
        raise ValueError(
            f"{raw.get('policy_id')}: concurrency must be positive"
        )

    endpoint_env = str(raw["endpoint_env"]).strip()
    if not endpoint_env.replace("_", "").isalnum():
        raise ValueError(
            f"{raw.get('policy_id')}: endpoint_env must be an environment variable name"
        )

    telemetry_required = bool(raw.get("telemetry_required", False))
    telemetry_env = str(raw.get("telemetry_env") or "").strip()
    if telemetry_required and not telemetry_env:
        raise ValueError(
            f"{raw.get('policy_id')}: telemetry_env is required when telemetry_required=true"
        )
    for field in (
        "telemetry_env",
        "telemetry_token_env",
        "expected_runtime_revision_env",
        "expected_launch_config_sha256_env",
    ):
        value = str(raw.get(field) or "").strip()
        if value and not value.replace("_", "").isalnum():
            raise ValueError(
                f"{raw.get('policy_id')}: {field} must be an environment variable name"
            )

    response_models = raw.get("accepted_response_models")
    if response_models is None:
        response_models = [str(raw["model_handle"])]
    if (
        not isinstance(response_models, list)
        or not response_models
        or not all(str(item).strip() for item in response_models)
    ):
        raise ValueError(
            f"{raw.get('policy_id')}: accepted_response_models must be a non-empty list"
        )

    normalized = {
        **raw,
        "policy_id": str(raw["policy_id"]),
        "node_id": str(raw["node_id"]),
        "model_handle": str(raw["model_handle"]),
        "backend": str(raw["backend"]),
        "quantization": str(raw["quantization"]),
        "speculation": str(raw["speculation"]),
        "context_tokens": context_tokens,
        "concurrency": concurrency,
        "endpoint_env": endpoint_env,
        "api_key_env": (
            str(raw["api_key_env"]).strip()
            if raw.get("api_key_env")
            else None
        ),
        "telemetry_required": telemetry_required,
        "telemetry_env": telemetry_env or None,
        "telemetry_token_env": (
            str(raw["telemetry_token_env"]).strip()
            if raw.get("telemetry_token_env")
            else None
        ),
        "expected_runtime_revision_env": (
            str(raw["expected_runtime_revision_env"]).strip()
            if raw.get("expected_runtime_revision_env")
            else None
        ),
        "expected_launch_config_sha256_env": (
            str(raw["expected_launch_config_sha256_env"]).strip()
            if raw.get("expected_launch_config_sha256_env")
            else None
        ),
        "enabled": bool(raw.get("enabled", True)),
        "allow_model_load": False,
        "execution_mode": "observe_only",
        "authority": dict(DEFAULT_AUTHORITY),
        "accepted_response_models": [str(item) for item in response_models],
    }
    normalized["policy_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in normalized.items()
            if key != "policy_sha256"
        }
    )
    return normalized


def compile_trials(
    cases: Iterable[dict[str, Any]],
    matrix: dict[str, Any],
) -> list[Trial]:
    trials: list[Trial] = []
    policies = [
        policy
        for policy in matrix.get("policies") or []
        if policy.get("enabled", True)
    ]
    for case in cases:
        for policy in policies:
            family_filter = policy.get("task_families")
            if (
                isinstance(family_filter, list)
                and family_filter
                and case["task_family"] not in {
                    _normalize_family(value)
                    for value in family_filter
                }
            ):
                continue
            digest = canonical_sha256(
                {
                    "case_sha256": case["case_sha256"],
                    "policy_sha256": policy["policy_sha256"],
                }
            )[:20]
            trials.append(
                Trial(
                    trial_id=f"trial-{digest}",
                    case=case,
                    policy=policy,
                )
            )
    return trials


def shadow_rows_to_cases(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        intent_id = str(row.get("intent_id") or f"row-{index}")
        text = str(row.get("text") or "").strip()
        if not text:
            continue

        trajectory = row.get("trajectory")
        if not isinstance(trajectory, dict):
            trajectory = {}
        verifications = trajectory.get("verifications")
        if not isinstance(verifications, list):
            verifications = []

        cases.append(
            {
                "case_id": f"assistx-intent-{intent_id}",
                "task_family": _normalize_family(
                    row.get("legacy_classification") or "general"
                ),
                "messages": [{"role": "user", "content": text}],
                "acceptance": {},
                "baseline": {
                    "legacy_classification": row.get(
                        "legacy_classification"
                    ),
                    "legacy_policy_action": row.get(
                        "legacy_policy_action"
                    ),
                    "policy_shadow_route": row.get(
                        "policy_shadow_route"
                    ),
                    "policy_shadow_disposition": row.get(
                        "policy_shadow_disposition"
                    ),
                    "recorded_verifications": verifications,
                },
                "source": "assistx-my-jev-shadow-export",
            }
        )
    return cases


def evaluate_acceptance(
    output_text: str,
    acceptance: dict[str, Any],
) -> tuple[bool | None, dict[str, Any]]:
    checks: dict[str, Any] = {}
    has_rule = False

    required_terms = acceptance.get("required_terms")
    if isinstance(required_terms, list) and required_terms:
        has_rule = True
        lower = output_text.lower()
        missing = [
            str(term)
            for term in required_terms
            if str(term).lower() not in lower
        ]
        checks["required_terms"] = {
            "passed": not missing,
            "missing": missing,
        }

    required_exact_terms = acceptance.get("required_exact_terms")
    if isinstance(required_exact_terms, list) and required_exact_terms:
        has_rule = True
        missing_exact = [
            str(term)
            for term in required_exact_terms
            if str(term) not in output_text
        ]
        checks["required_exact_terms"] = {
            "passed": not missing_exact,
            "missing": missing_exact,
        }

    json_keys = acceptance.get("json_required_keys")
    if isinstance(json_keys, list) and json_keys:
        has_rule = True
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError:
            parsed = None
        missing_keys = (
            [
                str(key)
                for key in json_keys
                if not isinstance(parsed, dict)
                or str(key) not in parsed
            ]
        )
        checks["json_required_keys"] = {
            "passed": not missing_keys,
            "missing": missing_keys,
        }

    task_evaluator = acceptance.get("task_evaluator")
    if isinstance(task_evaluator, dict):
        has_rule = True
        evaluator_passed, evaluator_details = evaluate_task_output(
            output_text,
            task_evaluator,
        )
        checks["task_evaluator"] = {
            **evaluator_details,
            "passed": evaluator_passed,
        }

    if not has_rule:
        return None, checks
    passed = all(
        bool(value.get("passed"))
        for value in checks.values()
    )
    return passed, checks


def execute_trial(
    trial: Trial,
    *,
    max_tokens: int = 256,
    timeout_s: float = 180.0,
    include_output: bool = False,
) -> dict[str, Any]:
    policy = trial.policy
    if int(policy["concurrency"]) != 1:
        raise ValueError(
            "phase-1 runner executes only concurrency=1 policies; "
            "use a later load-test stage for concurrent trials"
        )

    base_url = os.getenv(policy["endpoint_env"], "").strip()
    if not base_url:
        raise ValueError(
            f"{trial.trial_id}: environment variable "
            f"{policy['endpoint_env']} is not set"
        )
    url = _chat_completions_url(base_url)

    headers = {"Content-Type": "application/json"}
    api_key_env = policy.get("api_key_env")
    if api_key_env:
        key = os.getenv(str(api_key_env), "")
        if not key:
            raise ValueError(
                f"{trial.trial_id}: environment variable {api_key_env} is not set"
            )
        headers["Authorization"] = f"Bearer {key}"

    body: dict[str, Any] = {
        "model": policy["model_handle"],
        "messages": trial.case["messages"],
        "max_tokens": int(max_tokens),
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    overrides = policy.get("request_overrides")
    if isinstance(overrides, dict):
        forbidden = {
            "model",
            "messages",
            "stream",
            "stream_options",
        }.intersection(overrides)
        if forbidden:
            raise ValueError(
                f"{trial.trial_id}: request_overrides cannot replace "
                + ", ".join(sorted(forbidden))
            )
        body.update(overrides)

    started = time.perf_counter()
    first_content_at: float | None = None
    chunks: list[str] = []
    usage: dict[str, Any] = {}
    response_timings: dict[str, Any] = {}
    response_model: str | None = None
    finish_reason: str | None = None
    status_code: int | None = None
    error: str | None = None

    try:
        with requests.post(
            url,
            headers=headers,
            json=body,
            stream=True,
            timeout=timeout_s,
        ) as response:
            status_code = response.status_code
            response.raise_for_status()
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = str(raw_line).strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("model"):
                    response_model = str(event["model"])
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                if isinstance(event.get("timings"), dict):
                    response_timings = event["timings"]
                choices = event.get("choices")
                if not isinstance(choices, list):
                    continue
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
                    delta = choice.get("delta")
                    content = (
                        delta.get("content")
                        if isinstance(delta, dict)
                        else None
                    )
                    if isinstance(content, str) and content:
                        if first_content_at is None:
                            first_content_at = time.perf_counter()
                        chunks.append(content)
    except Exception as exc:
        error = str(exc)[:600]

    finished = time.perf_counter()
    output_text = "".join(chunks)
    ttft_ms = (
        round((first_content_at - started) * 1000, 3)
        if first_content_at is not None
        else None
    )
    wall_ms = round((finished - started) * 1000, 3)
    decode_window_ms = (
        round((finished - first_content_at) * 1000, 3)
        if first_content_at is not None
        else None
    )
    completion_tokens = _int_or_none(usage.get("completion_tokens"))
    prompt_tokens = _int_or_none(usage.get("prompt_tokens"))
    tokens_per_second = None
    if (
        completion_tokens is not None
        and decode_window_ms is not None
        and decode_window_ms > 0
    ):
        tokens_per_second = round(
            completion_tokens / (decode_window_ms / 1000),
            4,
        )

    accepted_models = set(policy["accepted_response_models"])
    model_match = (
        response_model in accepted_models
        if response_model is not None
        else None
    )
    acceptance_passed, acceptance_checks = evaluate_acceptance(
        output_text,
        trial.case.get("acceptance") or {},
    )
    success = (
        error is None
        and status_code is not None
        and 200 <= status_code < 300
        and model_match is not False
        and bool(output_text)
    )

    result = {
        "schema": "assistx-inference-policy-result-v1",
        "trial_id": trial.trial_id,
        "case_id": trial.case["case_id"],
        "case_sha256": trial.case["case_sha256"],
        "task_family": trial.case["task_family"],
        "evaluation_suite": trial.case.get("evaluation_suite"),
        "evaluator_kind": (
            (trial.case.get("acceptance") or {})
            .get("task_evaluator", {})
            .get("kind")
            if isinstance(
                (trial.case.get("acceptance") or {}).get(
                    "task_evaluator"
                ),
                dict,
            )
            else None
        ),
        "policy_id": policy["policy_id"],
        "policy_sha256": policy["policy_sha256"],
        "node_id": policy["node_id"],
        "model_handle": policy["model_handle"],
        "backend": policy["backend"],
        "quantization": policy["quantization"],
        "speculation": policy["speculation"],
        "context_tokens": policy["context_tokens"],
        "concurrency": policy["concurrency"],
        "execution_mode": "observe_only",
        "allow_model_load": False,
        "authority": dict(DEFAULT_AUTHORITY),
        "endpoint_env": policy["endpoint_env"],
        "http_status": status_code,
        "success": success,
        "error": error,
        "response_model": response_model,
        "model_match": model_match,
        "finish_reason": finish_reason,
        "ttft_ms": ttft_ms,
        "wall_ms": wall_ms,
        "decode_window_ms": decode_window_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tokens_per_second": tokens_per_second,
        "response_timings": response_timings,
        "acceptance_passed": acceptance_passed,
        "acceptance_checks": acceptance_checks,
        "output_sha256": hashlib.sha256(
            output_text.encode("utf-8")
        ).hexdigest(),
        "output_chars": len(output_text),
        "recorded_at_unix_ms": int(time.time() * 1000),
    }
    if include_output:
        result["output_text"] = output_text
    return result


def summarize_counterfactuals(
    results: Iterable[dict[str, Any]],
    *,
    baseline_policy_id: str | None = None,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        grouped.setdefault(str(row.get("case_id") or ""), []).append(row)

    cases: list[dict[str, Any]] = []
    for case_id, rows in sorted(grouped.items()):
        eligible = [
            row
            for row in rows
            if row.get("success") is True
            and row.get("acceptance_passed") is True
            and (
                row.get("telemetry_required") is not True
                or row.get("telemetry_valid") is True
            )
            and isinstance(row.get("wall_ms"), (int, float))
        ]
        best = min(
            eligible,
            key=lambda row: float(row["wall_ms"]),
            default=None,
        )
        baseline = None
        if baseline_policy_id:
            baseline = next(
                (
                    row
                    for row in rows
                    if row.get("policy_id") == baseline_policy_id
                ),
                None,
            )

        speedup = None
        if (
            best is not None
            and baseline is not None
            and baseline.get("success") is True
            and baseline.get("acceptance_passed") is True
            and isinstance(baseline.get("wall_ms"), (int, float))
            and float(best["wall_ms"]) > 0
        ):
            speedup = round(
                float(baseline["wall_ms"]) / float(best["wall_ms"]),
                4,
            )

        cases.append(
            {
                "case_id": case_id,
                "eligible_policy_count": len(eligible),
                "selected_policy_id": baseline_policy_id,
                "selected_policy_score": _policy_score(baseline),
                "best_observed_policy_id": (
                    best.get("policy_id") if best else None
                ),
                "best_observed_policy_score": _policy_score(best),
                "best_observed_wall_ms": (
                    best.get("wall_ms") if best else None
                ),
                "baseline_policy_id": baseline_policy_id,
                "baseline_wall_ms": (
                    baseline.get("wall_ms")
                    if baseline is not None
                    else None
                ),
                "baseline_to_best_speedup": speedup,
                "routing_authority_changed": False,
            }
        )

    return {
        "schema": "assistx-inference-policy-counterfactual-v1",
        "case_count": len(cases),
        "cases": cases,
        "authority": dict(DEFAULT_AUTHORITY),
        "note": (
            "Counterfactual evidence only. No result is promoted into "
            "AssistX routing or execution authority."
        ),
    }


def _policy_score(row: dict[str, Any] | None) -> float | None:
    """Quality-gated inverse latency score for phase-1 counterfactuals."""
    if row is None:
        return None
    accepted = row.get("acceptance_passed")
    wall_ms = row.get("wall_ms")
    if accepted is None or not isinstance(wall_ms, (int, float)):
        return None
    if accepted is not True or float(wall_ms) <= 0:
        return 0.0
    return round(1000.0 / float(wall_ms), 8)


def _chat_completions_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/v1/chat/completions"):
        return value
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


def _normalize_family(value: Any) -> str:
    normalized = (
        str(value or "general")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    return _FAMILY_ALIASES.get(normalized, normalized or "general")


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
