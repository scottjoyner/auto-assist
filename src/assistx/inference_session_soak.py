from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

from .inference_policy_experiment import (
    DEFAULT_AUTHORITY,
    Trial,
    canonical_sha256,
    evaluate_acceptance,
    execute_trial,
)
from .runtime_telemetry_observer import (
    capture_runtime_snapshot,
    correlate_join_with_result,
    join_runtime_snapshots,
)

SOAK_PROFILE_SCHEMA = "assistx-inference-session-soak-profile-v1"
SOAK_PLAN_SCHEMA = "assistx-inference-session-soak-plan-v1"
SOAK_RESULT_SCHEMA = "assistx-inference-session-soak-result-v1"
SOAK_SUMMARY_SCHEMA = "assistx-inference-session-soak-summary-v1"
SOAK_STATE_SCHEMA = "assistx-inference-session-soak-state-v1"

MODES = {"stable_prefix", "growing_prefix"}

_TURN_CLASSES = (
    "coding",
    "tool_json",
    "reasoning",
    "prose",
)


def load_soak_profile(
    path: str | Path,
    profile_id: str,
) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("soak profile file must be an object")
    profiles = raw.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles must be a non-empty list")
    for value in profiles:
        if (
            isinstance(value, dict)
            and str(value.get("profile_id") or "") == profile_id
        ):
            return validate_soak_profile(value)
    raise ValueError(f"unknown soak profile_id: {profile_id}")


def validate_soak_profile(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("soak profile must be an object")

    profile_id = str(raw.get("profile_id") or "").strip()
    if not profile_id:
        raise ValueError("profile_id is required")

    target_context_tokens = int(raw.get("target_context_tokens") or 0)
    if target_context_tokens < 4096:
        raise ValueError("target_context_tokens must be at least 4096")

    default_turns = int(raw.get("default_turns") or 100)
    min_turns = int(raw.get("min_turns") or 20)
    max_turns = int(raw.get("max_turns") or 500)
    if min_turns < 1 or max_turns < min_turns:
        raise ValueError("invalid min_turns/max_turns")
    if not min_turns <= default_turns <= max_turns:
        raise ValueError("default_turns must be inside min/max bounds")

    canary_every = int(raw.get("canary_every") or 10)
    if canary_every < 1:
        raise ValueError("canary_every must be positive")

    marker = str(
        raw.get("retention_marker")
        or f"ASSISTX-SOAK-{profile_id.upper()}"
    ).strip()
    if not marker:
        raise ValueError("retention_marker cannot be empty")

    chars_per_token = float(raw.get("prefix_chars_per_token") or 4.2)
    if not 1.0 <= chars_per_token <= 12.0:
        raise ValueError("prefix_chars_per_token must be between 1 and 12")

    stable_fraction = float(raw.get("stable_prefix_fraction") or 0.90)
    growing_fraction = float(raw.get("growing_prefix_fraction") or 0.75)
    for name, value in (
        ("stable_prefix_fraction", stable_fraction),
        ("growing_prefix_fraction", growing_fraction),
    ):
        if not 0.1 <= value <= 0.98:
            raise ValueError(f"{name} must be between 0.1 and 0.98")

    thresholds = raw.get("thresholds")
    if thresholds is None:
        thresholds = {}
    if not isinstance(thresholds, dict):
        raise ValueError("thresholds must be an object")

    normalized = {
        **raw,
        "schema": SOAK_PROFILE_SCHEMA,
        "profile_id": profile_id,
        "target_context_tokens": target_context_tokens,
        "default_turns": default_turns,
        "min_turns": min_turns,
        "max_turns": max_turns,
        "canary_every": canary_every,
        "retention_marker": marker,
        "prefix_chars_per_token": chars_per_token,
        "stable_prefix_fraction": stable_fraction,
        "growing_prefix_fraction": growing_fraction,
        "thresholds": {
            "min_success_rate": float(
                thresholds.get("min_success_rate", 0.99)
            ),
            "min_acceptance_rate": float(
                thresholds.get("min_acceptance_rate", 0.98)
            ),
            "min_canary_pass_rate": float(
                thresholds.get("min_canary_pass_rate", 1.0)
            ),
            "min_telemetry_valid_rate": float(
                thresholds.get("min_telemetry_valid_rate", 1.0)
            ),
            "max_ttft_drift_ratio": float(
                thresholds.get("max_ttft_drift_ratio", 2.0)
            ),
            "max_wall_drift_ratio": float(
                thresholds.get("max_wall_drift_ratio", 2.0)
            ),
            "max_vram_growth_bytes": int(
                thresholds.get(
                    "max_vram_growth_bytes",
                    2 * 1024 * 1024 * 1024,
                )
            ),
        },
    }

    for name in (
        "min_success_rate",
        "min_acceptance_rate",
        "min_canary_pass_rate",
        "min_telemetry_valid_rate",
    ):
        value = normalized["thresholds"][name]
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"thresholds.{name} must be between 0 and 1")

    for name in ("max_ttft_drift_ratio", "max_wall_drift_ratio"):
        if normalized["thresholds"][name] < 1.0:
            raise ValueError(f"thresholds.{name} must be >= 1")

    if normalized["thresholds"]["max_vram_growth_bytes"] < 0:
        raise ValueError(
            "thresholds.max_vram_growth_bytes cannot be negative"
        )

    normalized["profile_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in normalized.items()
            if key != "profile_sha256"
        }
    )
    return normalized


def compile_soak_plan(
    profile: dict[str, Any],
    policy: dict[str, Any],
    *,
    mode: str,
    turns: int | None = None,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(
            f"mode must be one of: {', '.join(sorted(MODES))}"
        )
    requested_turns = (
        int(turns)
        if turns is not None
        else int(profile["default_turns"])
    )
    if not int(profile["min_turns"]) <= requested_turns <= int(
        profile["max_turns"]
    ):
        raise ValueError(
            f"turns must be between {profile['min_turns']} "
            f"and {profile['max_turns']}"
        )
    if int(policy.get("concurrency") or 0) != 1:
        raise ValueError("session soak requires concurrency=1")
    if int(policy.get("context_tokens") or 0) < int(
        profile["target_context_tokens"]
    ):
        raise ValueError(
            "policy context_tokens is smaller than soak target_context_tokens"
        )
    if policy.get("telemetry_required") is not True:
        raise ValueError(
            "session soak requires telemetry_required=true"
        )

    seed = build_seed_context(profile, mode=mode)
    session_id = "soak-" + canonical_sha256(
        {
            "profile_sha256": profile["profile_sha256"],
            "policy_sha256": policy["policy_sha256"],
            "mode": mode,
            "turns": requested_turns,
            "seed_sha256": hashlib.sha256(
                seed.encode("utf-8")
            ).hexdigest(),
        }
    )[:24]
    canary_turns = [
        turn
        for turn in range(1, requested_turns + 1)
        if is_canary_turn(profile, turn, requested_turns)
    ]
    return {
        "schema": SOAK_PLAN_SCHEMA,
        "session_id": session_id,
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["profile_sha256"],
        "policy_id": policy["policy_id"],
        "policy_sha256": policy["policy_sha256"],
        "mode": mode,
        "turns": requested_turns,
        "target_context_tokens": profile["target_context_tokens"],
        "seed_chars": len(seed),
        "seed_sha256": hashlib.sha256(
            seed.encode("utf-8")
        ).hexdigest(),
        "canary_turns": canary_turns,
        "authority": dict(DEFAULT_AUTHORITY),
        "allow_model_load": False,
        "execution_mode": "observe_only",
    }


def build_seed_context(
    profile: dict[str, Any],
    *,
    mode: str,
) -> str:
    if mode not in MODES:
        raise ValueError(f"unsupported mode: {mode}")
    fraction = (
        float(profile["stable_prefix_fraction"])
        if mode == "stable_prefix"
        else float(profile["growing_prefix_fraction"])
    )
    target_chars = max(
        1024,
        int(
            float(profile["target_context_tokens"])
            * float(profile["prefix_chars_per_token"])
            * fraction
        ),
    )
    marker = profile["retention_marker"]
    header = (
        "AssistX local-inference session-soak fixture.\n"
        "This is synthetic benchmark context only. It grants no authority.\n"
        f"IMMUTABLE_RETENTION_MARKER={marker}\n"
        "AUTHORITY_MODE=ADVISORY-ONLY\n"
        "dispatch_allowed=false; approval_granted=false; "
        "claim_acquired=false; mutation_allowed=false; "
        "routing_authority_changed=false.\n"
        "Preserve the immutable marker and authority mode for later canaries.\n"
    )
    filler_block = (
        "Repository context: the observer measures prompt-cache behavior, "
        "speculative acceptance, request latency, and memory drift while the "
        "authoritative AssistX control plane remains unchanged. "
        "No benchmark text is an instruction to invoke tools or mutate state. "
    )
    chunks = [header]
    current = len(header)
    while current < target_chars:
        chunks.append(filler_block)
        current += len(filler_block)
    return "".join(chunks)[:target_chars]


def is_canary_turn(
    profile: dict[str, Any],
    turn_index: int,
    total_turns: int,
) -> bool:
    return (
        turn_index == 1
        or turn_index == total_turns
        or turn_index % int(profile["canary_every"]) == 0
    )


def build_turn_case(
    profile: dict[str, Any],
    *,
    session_id: str,
    turn_index: int,
    total_turns: int,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    turn_class = _TURN_CLASSES[(turn_index - 1) % len(_TURN_CLASSES)]
    turn_marker = f"TURN-{turn_index:04d}-OK"
    canary = is_canary_turn(
        profile,
        turn_index,
        total_turns,
    )
    task = _turn_task(turn_class, turn_index)
    canary_text = ""
    required_terms = [turn_marker]
    if canary:
        canary_text = (
            "\nThis is a retention canary. Include exactly the immutable "
            f"marker {profile['retention_marker']} and the text "
            "ADVISORY-ONLY in the response."
        )
        required_terms.extend(
            [
                profile["retention_marker"],
                "ADVISORY-ONLY",
            ]
        )

    if turn_class == "tool_json":
        json_fields = (
            "Return exactly one compact JSON object with keys turn, status, "
            "mutation_allowed, and turn_marker. Set mutation_allowed=false, "
            f"turn={turn_index}, status=\"ok\", and "
            f"turn_marker=\"{turn_marker}\"."
        )
        if canary:
            json_fields += (
                " Also include retention_marker="
                f"\"{profile['retention_marker']}\" and "
                "authority_mode=\"ADVISORY-ONLY\"."
            )
        response_instruction = json_fields
    else:
        response_instruction = (
            task
            + canary_text
            + "\nEnd the response with "
            + turn_marker
            + "."
        )

    prompt = (
        f"Synthetic AssistX soak turn {turn_index}/{total_turns}. "
        f"Workload class: {turn_class}. {response_instruction}\n"
        "Do not invoke tools, claim work, grant approval, or mutate state."
    )
    turn_messages = [
        *messages,
        {"role": "user", "content": prompt},
    ]
    case_id = f"{session_id}-turn-{turn_index:04d}"
    acceptance = {"required_terms": required_terms}
    case = {
        "case_id": case_id,
        "task_family": (
            "tool_use"
            if turn_class == "tool_json"
            else turn_class
        ),
        "messages": turn_messages,
        "acceptance": acceptance,
        "soak": {
            "session_id": session_id,
            "turn_index": turn_index,
            "turn_class": turn_class,
            "canary": canary,
            "turn_marker": turn_marker,
        },
    }
    case["case_sha256"] = canonical_sha256(
        {
            "case_id": case_id,
            "task_family": case["task_family"],
            "messages": turn_messages,
            "acceptance": acceptance,
            "soak": case["soak"],
        }
    )
    return case


def execute_soak_turn(
    profile: dict[str, Any],
    policy: dict[str, Any],
    *,
    session_id: str,
    turn_index: int,
    total_turns: int,
    messages: list[dict[str, str]],
    max_tokens: int = 192,
    timeout_s: float = 240.0,
    telemetry_timeout_s: float = 5.0,
) -> tuple[dict[str, Any], str | None]:
    case = build_turn_case(
        profile,
        session_id=session_id,
        turn_index=turn_index,
        total_turns=total_turns,
        messages=messages,
    )
    trial = Trial(
        trial_id=case["case_id"],
        case=case,
        policy=policy,
    )

    before = capture_runtime_snapshot(
        policy,
        trial_id=trial.trial_id,
        timeout_s=telemetry_timeout_s,
    )
    if not before.get("valid"):
        return (
            _failed_soak_result(
                profile,
                policy,
                case,
                reason=(
                    "required telemetry preflight failed: "
                    + str(before.get("reason") or "unknown")
                ),
                runtime_telemetry=before,
            ),
            None,
        )

    result = execute_trial(
        trial,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        include_output=True,
    )
    output_text = result.pop("output_text", None)

    after = capture_runtime_snapshot(
        policy,
        trial_id=trial.trial_id,
        timeout_s=telemetry_timeout_s,
    )
    joined = join_runtime_snapshots(
        before,
        after,
        policy,
        trial_id=trial.trial_id,
    )
    joined = correlate_join_with_result(
        joined,
        result,
    )

    result.update(
        {
            "schema": SOAK_RESULT_SCHEMA,
            "session_id": session_id,
            "profile_id": profile["profile_id"],
            "profile_sha256": profile["profile_sha256"],
            "turn_index": turn_index,
            "turn_class": case["soak"]["turn_class"],
            "canary": case["soak"]["canary"],
            "turn_marker": case["soak"]["turn_marker"],
            "telemetry_required": True,
            "telemetry_valid": bool(joined.get("valid")),
            "runtime_telemetry": joined,
            "authority": dict(DEFAULT_AUTHORITY),
        }
    )
    if not joined.get("valid"):
        result["success"] = False
        telemetry_error = str(
            joined.get("reason") or "runtime telemetry invalid"
        )
        existing = str(result.get("error") or "").strip()
        result["error"] = (
            existing
            + ("; " if existing else "")
            + telemetry_error
        )[:600]

    return result, (
        output_text
        if isinstance(output_text, str) and output_text
        else None
    )


def initial_state(
    plan: dict[str, Any],
) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "schema": SOAK_STATE_SCHEMA,
        "session_id": plan["session_id"],
        "profile_sha256": plan["profile_sha256"],
        "policy_sha256": plan["policy_sha256"],
        "mode": plan["mode"],
        "turns": plan["turns"],
        "completed_turns": 0,
        "runtime_identity": None,
        "history": [],
        "created_at_unix_ms": now,
        "updated_at_unix_ms": now,
    }


def validate_resume_state(
    state: Any,
    plan: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("resume state must be an object")
    if state.get("schema") != SOAK_STATE_SCHEMA:
        raise ValueError("resume state schema mismatch")
    for field in (
        "session_id",
        "profile_sha256",
        "policy_sha256",
        "mode",
        "turns",
    ):
        if state.get(field) != plan.get(field):
            raise ValueError(
                f"resume state {field} does not match compiled plan"
            )
    completed = int(state.get("completed_turns") or 0)
    if not 0 <= completed <= int(plan["turns"]):
        raise ValueError("resume state completed_turns is invalid")
    history = state.get("history")
    if not isinstance(history, list):
        raise ValueError("resume state history must be a list")
    return state


def save_state(
    path: str | Path,
    state: dict[str, Any],
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    value = {
        **state,
        "updated_at_unix_ms": int(time.time() * 1000),
    }
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temp.replace(output)


def load_state(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("state file must be an object")
    return value


def check_session_runtime_identity(
    state: dict[str, Any],
    result: dict[str, Any],
) -> tuple[bool, str | None]:
    telemetry = result.get("runtime_telemetry")
    if not isinstance(telemetry, dict) or not telemetry.get("valid"):
        return False, "runtime telemetry is not valid"
    identity = {
        "runtime_revision": telemetry.get("runtime_revision"),
        "launch_config_sha256": telemetry.get("launch_config_sha256"),
        "process_started_at_unix_ms": telemetry.get(
            "process_started_at_unix_ms"
        ),
    }
    if any(value in (None, "") for value in identity.values()):
        return False, "runtime telemetry is missing session identity"

    existing = state.get("runtime_identity")
    if existing is None:
        state["runtime_identity"] = identity
        return True, None
    if existing != identity:
        return False, "runtime process identity changed during soak session"
    return True, None


def append_history(
    state: dict[str, Any],
    case: dict[str, Any],
    output_text: str | None,
) -> None:
    if state.get("mode") != "growing_prefix":
        return
    if not output_text:
        return
    user = case["messages"][-1]
    state["history"].append(
        {
            "role": "user",
            "content": str(user["content"]),
        }
    )
    state["history"].append(
        {
            "role": "assistant",
            "content": output_text,
        }
    )


def messages_for_turn(
    profile: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, str]]:
    seed = build_seed_context(
        profile,
        mode=str(state["mode"]),
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": seed}
    ]
    if state["mode"] == "growing_prefix":
        for item in state.get("history") or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")
            if role in {"user", "assistant"} and content:
                messages.append(
                    {"role": role, "content": content}
                )
    return messages


def summarize_soak_results(
    profile: dict[str, Any],
    plan: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    total = len(rows)
    expected = int(plan["turns"])
    success_count = sum(
        1 for row in rows if row.get("success") is True
    )
    accepted_rows = [
        row
        for row in rows
        if row.get("acceptance_passed") is True
    ]
    canaries = [
        row for row in rows if row.get("canary") is True
    ]
    canary_passed = [
        row
        for row in canaries
        if row.get("acceptance_passed") is True
    ]
    telemetry_valid = [
        row
        for row in rows
        if row.get("telemetry_valid") is True
    ]

    ttft = _numeric_values(rows, "ttft_ms")
    wall = _numeric_values(rows, "wall_ms")
    prompt_tokens = _numeric_values(rows, "prompt_tokens")
    completion_tokens = _numeric_values(rows, "completion_tokens")
    vram = _vram_after_values(rows)
    cache_reuse = _derived_values(rows, "cache_reuse_rate")
    spec_acceptance = _derived_values(
        rows,
        "spec_acceptance_rate",
    )

    success_rate = _fraction(success_count, total)
    acceptance_rate = _fraction(len(accepted_rows), total)
    canary_pass_rate = _fraction(
        len(canary_passed),
        len(canaries),
    )
    telemetry_valid_rate = _fraction(
        len(telemetry_valid),
        total,
    )

    ttft_drift = _drift_ratio(ttft)
    wall_drift = _drift_ratio(wall)
    vram_growth = (
        int(vram[-1] - vram[0])
        if len(vram) >= 2
        else None
    )
    vram_slope = _simple_slope(vram)

    thresholds = profile["thresholds"]
    gates = {
        "turn_count_complete": {
            "passed": total == expected,
            "observed": total,
            "required": expected,
        },
        "success_rate": _minimum_gate(
            success_rate,
            thresholds["min_success_rate"],
        ),
        "acceptance_rate": _minimum_gate(
            acceptance_rate,
            thresholds["min_acceptance_rate"],
        ),
        "canary_pass_rate": _minimum_gate(
            canary_pass_rate,
            thresholds["min_canary_pass_rate"],
        ),
        "telemetry_valid_rate": _minimum_gate(
            telemetry_valid_rate,
            thresholds["min_telemetry_valid_rate"],
        ),
        "ttft_drift_ratio": _maximum_gate(
            ttft_drift,
            thresholds["max_ttft_drift_ratio"],
        ),
        "wall_drift_ratio": _maximum_gate(
            wall_drift,
            thresholds["max_wall_drift_ratio"],
        ),
        "vram_growth_bytes": _maximum_gate(
            (
                float(vram_growth)
                if vram_growth is not None
                else None
            ),
            float(thresholds["max_vram_growth_bytes"]),
            allow_missing=True,
        ),
    }
    passed = all(
        gate.get("passed") is not False
        for gate in gates.values()
    )

    by_class: dict[str, dict[str, Any]] = {}
    for turn_class in _TURN_CLASSES:
        class_rows = [
            row
            for row in rows
            if row.get("turn_class") == turn_class
        ]
        if not class_rows:
            continue
        by_class[turn_class] = {
            "turns": len(class_rows),
            "acceptance_rate": _fraction(
                sum(
                    1
                    for row in class_rows
                    if row.get("acceptance_passed") is True
                ),
                len(class_rows),
            ),
            "ttft_p50_ms": _percentile(
                _numeric_values(class_rows, "ttft_ms"),
                0.50,
            ),
            "wall_p50_ms": _percentile(
                _numeric_values(class_rows, "wall_ms"),
                0.50,
            ),
            "spec_acceptance_mean": _mean_or_none(
                _derived_values(
                    class_rows,
                    "spec_acceptance_rate",
                )
            ),
            "cache_reuse_mean": _mean_or_none(
                _derived_values(
                    class_rows,
                    "cache_reuse_rate",
                )
            ),
        }

    return {
        "schema": SOAK_SUMMARY_SCHEMA,
        "session_id": plan["session_id"],
        "profile_id": plan["profile_id"],
        "profile_sha256": plan["profile_sha256"],
        "policy_id": plan["policy_id"],
        "policy_sha256": plan["policy_sha256"],
        "mode": plan["mode"],
        "target_context_tokens": plan["target_context_tokens"],
        "expected_turns": expected,
        "completed_turns": total,
        "passed": passed,
        "rates": {
            "success": success_rate,
            "acceptance": acceptance_rate,
            "canary_pass": canary_pass_rate,
            "telemetry_valid": telemetry_valid_rate,
        },
        "latency": {
            "ttft_p50_ms": _percentile(ttft, 0.50),
            "ttft_p95_ms": _percentile(ttft, 0.95),
            "ttft_drift_ratio": ttft_drift,
            "wall_p50_ms": _percentile(wall, 0.50),
            "wall_p95_ms": _percentile(wall, 0.95),
            "wall_drift_ratio": wall_drift,
        },
        "context": {
            "prompt_tokens_first": (
                prompt_tokens[0] if prompt_tokens else None
            ),
            "prompt_tokens_last": (
                prompt_tokens[-1] if prompt_tokens else None
            ),
            "prompt_tokens_min": (
                min(prompt_tokens) if prompt_tokens else None
            ),
            "prompt_tokens_max": (
                max(prompt_tokens) if prompt_tokens else None
            ),
            "completion_tokens_total": (
                sum(completion_tokens)
                if completion_tokens
                else None
            ),
        },
        "cache_and_speculation": {
            "cache_reuse_mean": _mean_or_none(cache_reuse),
            "cache_reuse_last": (
                cache_reuse[-1] if cache_reuse else None
            ),
            "spec_acceptance_mean": _mean_or_none(
                spec_acceptance
            ),
            "spec_acceptance_last": (
                spec_acceptance[-1]
                if spec_acceptance
                else None
            ),
        },
        "memory": {
            "vram_first_bytes": vram[0] if vram else None,
            "vram_last_bytes": vram[-1] if vram else None,
            "vram_max_bytes": max(vram) if vram else None,
            "vram_growth_bytes": vram_growth,
            "vram_slope_bytes_per_turn": vram_slope,
        },
        "by_turn_class": by_class,
        "gates": gates,
        "authority": dict(DEFAULT_AUTHORITY),
        "routing_authority_changed": False,
    }


def compare_soak_summaries(
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build an evidence-only comparison across completed soak summaries."""
    rows: list[dict[str, Any]] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        if summary.get("schema") != SOAK_SUMMARY_SCHEMA:
            raise ValueError("unexpected soak summary schema")
        rows.append(
            {
                "session_id": summary.get("session_id"),
                "profile_id": summary.get("profile_id"),
                "policy_id": summary.get("policy_id"),
                "mode": summary.get("mode"),
                "target_context_tokens": summary.get(
                    "target_context_tokens"
                ),
                "passed": summary.get("passed"),
                "completed_turns": summary.get("completed_turns"),
                "acceptance_rate": (
                    summary.get("rates") or {}
                ).get("acceptance"),
                "canary_pass_rate": (
                    summary.get("rates") or {}
                ).get("canary_pass"),
                "telemetry_valid_rate": (
                    summary.get("rates") or {}
                ).get("telemetry_valid"),
                "ttft_p50_ms": (
                    summary.get("latency") or {}
                ).get("ttft_p50_ms"),
                "ttft_p95_ms": (
                    summary.get("latency") or {}
                ).get("ttft_p95_ms"),
                "ttft_drift_ratio": (
                    summary.get("latency") or {}
                ).get("ttft_drift_ratio"),
                "wall_p50_ms": (
                    summary.get("latency") or {}
                ).get("wall_p50_ms"),
                "wall_drift_ratio": (
                    summary.get("latency") or {}
                ).get("wall_drift_ratio"),
                "prompt_tokens_first": (
                    summary.get("context") or {}
                ).get("prompt_tokens_first"),
                "prompt_tokens_last": (
                    summary.get("context") or {}
                ).get("prompt_tokens_last"),
                "cache_reuse_mean": (
                    summary.get("cache_and_speculation") or {}
                ).get("cache_reuse_mean"),
                "spec_acceptance_mean": (
                    summary.get("cache_and_speculation") or {}
                ).get("spec_acceptance_mean"),
                "vram_growth_bytes": (
                    summary.get("memory") or {}
                ).get("vram_growth_bytes"),
                "vram_slope_bytes_per_turn": (
                    summary.get("memory") or {}
                ).get("vram_slope_bytes_per_turn"),
            }
        )

    pairs: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, Any], dict[str, dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row.get("profile_id") or ""),
            str(row.get("policy_id") or ""),
            row.get("target_context_tokens"),
        )
        mode = str(row.get("mode") or "")
        grouped.setdefault(key, {})[mode] = row

    for key, modes in sorted(grouped.items()):
        stable = modes.get("stable_prefix")
        growing = modes.get("growing_prefix")
        if stable is None or growing is None:
            continue
        pairs.append(
            {
                "profile_id": key[0],
                "policy_id": key[1],
                "target_context_tokens": key[2],
                "both_passed": (
                    stable.get("passed") is True
                    and growing.get("passed") is True
                ),
                "stable_session_id": stable.get("session_id"),
                "growing_session_id": growing.get("session_id"),
                "growing_vs_stable": {
                    "ttft_p50_ratio": _safe_ratio(
                        growing.get("ttft_p50_ms"),
                        stable.get("ttft_p50_ms"),
                    ),
                    "wall_p50_ratio": _safe_ratio(
                        growing.get("wall_p50_ms"),
                        stable.get("wall_p50_ms"),
                    ),
                    "cache_reuse_delta": _safe_delta(
                        growing.get("cache_reuse_mean"),
                        stable.get("cache_reuse_mean"),
                    ),
                    "spec_acceptance_delta": _safe_delta(
                        growing.get("spec_acceptance_mean"),
                        stable.get("spec_acceptance_mean"),
                    ),
                    "vram_growth_delta_bytes": _safe_delta(
                        growing.get("vram_growth_bytes"),
                        stable.get("vram_growth_bytes"),
                    ),
                    "ttft_drift_delta": _safe_delta(
                        growing.get("ttft_drift_ratio"),
                        stable.get("ttft_drift_ratio"),
                    ),
                },
            }
        )

    return {
        "schema": "assistx-inference-session-soak-comparison-v1",
        "summaries": rows,
        "stable_growing_pairs": pairs,
        "routing_authority_changed": False,
        "note": (
            "Comparison is evidence only. It does not select or promote "
            "a production inference policy."
        ),
    }


def _safe_ratio(
    numerator: Any,
    denominator: Any,
) -> float | None:
    if not isinstance(numerator, (int, float)):
        return None
    if not isinstance(denominator, (int, float)):
        return None
    if float(denominator) <= 0:
        return None
    return round(float(numerator) / float(denominator), 9)


def _safe_delta(
    left: Any,
    right: Any,
) -> float | None:
    if not isinstance(left, (int, float)):
        return None
    if not isinstance(right, (int, float)):
        return None
    return round(float(left) - float(right), 9)


def _failed_soak_result(
    profile: dict[str, Any],
    policy: dict[str, Any],
    case: dict[str, Any],
    *,
    reason: str,
    runtime_telemetry: dict[str, Any],
) -> dict[str, Any]:
    soak = case["soak"]
    return {
        "schema": SOAK_RESULT_SCHEMA,
        "session_id": soak["session_id"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["profile_sha256"],
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "turn_index": soak["turn_index"],
        "turn_class": soak["turn_class"],
        "canary": soak["canary"],
        "turn_marker": soak["turn_marker"],
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
        "success": False,
        "error": reason[:600],
        "acceptance_passed": None,
        "telemetry_required": True,
        "telemetry_valid": False,
        "runtime_telemetry": runtime_telemetry,
    }


def _turn_task(turn_class: str, turn_index: int) -> str:
    if turn_class == "coding":
        return (
            "Give one concise invariant for a retry-safe local job queue and "
            "one sentence explaining why it matters."
        )
    if turn_class == "tool_json":
        return (
            "Return a compact JSON object describing a read-only health "
            f"observation with turn={turn_index}, status='ok', "
            "and mutation_allowed=false."
        )
    if turn_class == "reasoning":
        return (
            "Explain briefly why preserving exact runtime identity makes "
            "performance comparisons more trustworthy."
        )
    return (
        "Write one short operational note saying the benchmark is advisory "
        "and cannot change production authority."
    )


def _numeric_values(
    rows: list[dict[str, Any]],
    field: str,
) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(field)
        if isinstance(value, (int, float)) and not isinstance(
            value,
            bool,
        ):
            values.append(float(value))
    return values


def _vram_after_values(
    rows: list[dict[str, Any]],
) -> list[float]:
    values: list[float] = []
    for row in rows:
        telemetry = row.get("runtime_telemetry")
        if not isinstance(telemetry, dict):
            continue
        gauges = telemetry.get("gauge_samples")
        if not isinstance(gauges, dict):
            continue
        vram = gauges.get("vram_bytes")
        if not isinstance(vram, dict):
            continue
        value = vram.get("after")
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _derived_values(
    rows: list[dict[str, Any]],
    field: str,
) -> list[float]:
    values: list[float] = []
    for row in rows:
        telemetry = row.get("runtime_telemetry")
        if not isinstance(telemetry, dict):
            continue
        derived = telemetry.get("derived")
        if not isinstance(derived, dict):
            continue
        value = derived.get(field)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _fraction(
    numerator: int,
    denominator: int,
) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 9)


def _mean_or_none(
    values: list[float],
) -> float | None:
    if not values:
        return None
    return round(statistics.fmean(values), 9)


def _percentile(
    values: list[float],
    fraction: float,
) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 6)
    weight = position - lower
    value = (
        ordered[lower] * (1.0 - weight)
        + ordered[upper] * weight
    )
    return round(value, 6)


def _drift_ratio(
    values: list[float],
) -> float | None:
    if len(values) < 4:
        return None
    width = max(1, len(values) // 10)
    first = statistics.median(values[:width])
    last = statistics.median(values[-width:])
    if first <= 0:
        return None
    return round(last / first, 9)


def _simple_slope(
    values: list[float],
) -> float | None:
    if len(values) < 2:
        return None
    return round(
        (values[-1] - values[0]) / (len(values) - 1),
        6,
    )


def _minimum_gate(
    observed: float | None,
    required: float,
) -> dict[str, Any]:
    return {
        "passed": (
            None if observed is None else observed >= required
        ),
        "observed": observed,
        "required_minimum": required,
    }


def _maximum_gate(
    observed: float | None,
    maximum: float,
    *,
    allow_missing: bool = False,
) -> dict[str, Any]:
    return {
        "passed": (
            None
            if observed is None and allow_missing
            else (
                False
                if observed is None
                else observed <= maximum
            )
        ),
        "observed": observed,
        "required_maximum": maximum,
    }
