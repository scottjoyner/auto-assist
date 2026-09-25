from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .inference_policy_experiment import DEFAULT_AUTHORITY, canonical_sha256
from .inference_session_soak import (
    SOAK_SUMMARY_SCHEMA,
    load_soak_profile,
)

CAMPAIGN_PLAN_SCHEMA = "assistx-inference-soak-campaign-plan-v1"
CAMPAIGN_EVIDENCE_SCHEMA = "assistx-inference-soak-campaign-evidence-v1"

MODES = ("stable_prefix", "growing_prefix")


def load_campaign_config(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("campaign config must be an object")
    return validate_campaign_config(raw)


def validate_campaign_config(raw: dict[str, Any]) -> dict[str, Any]:
    campaign_id = str(raw.get("campaign_id") or "").strip()
    if not campaign_id:
        raise ValueError("campaign_id is required")
    source_context_tokens = int(raw.get("source_context_tokens") or 0)
    target_context_tokens = int(raw.get("target_context_tokens") or 0)
    if source_context_tokens <= 0 or target_context_tokens <= 0:
        raise ValueError("campaign context sizes must be positive")
    if target_context_tokens <= source_context_tokens:
        raise ValueError(
            "target_context_tokens must exceed source_context_tokens"
        )
    turns = int(raw.get("turns") or 100)
    if not 20 <= turns <= 500:
        raise ValueError("turns must be between 20 and 500")
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("nodes must be a non-empty list")
    speculations = raw.get("speculations")
    if not isinstance(speculations, list) or not speculations:
        raise ValueError("speculations must be a non-empty list")
    normalized = {
        **raw,
        "schema": "assistx-inference-soak-campaign-config-v1",
        "campaign_id": campaign_id,
        "source_context_tokens": source_context_tokens,
        "target_context_tokens": target_context_tokens,
        "turns": turns,
        "nodes": [str(item) for item in nodes],
        "speculations": [str(item) for item in speculations],
        "require_fresh_process_pair": bool(
            raw.get("require_fresh_process_pair", True)
        ),
        "require_same_runtime_revision": bool(
            raw.get("require_same_runtime_revision", True)
        ),
        "require_same_launch_config": bool(
            raw.get("require_same_launch_config", True)
        ),
    }
    normalized["config_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in normalized.items()
            if key != "config_sha256"
        }
    )
    return normalized


def compile_campaign_plan(
    config: dict[str, Any],
    matrix: dict[str, Any],
    *,
    profiles_file: str,
    matrix_file: str,
) -> dict[str, Any]:
    source_profile_id = _profile_id(config["source_context_tokens"])
    target_profile_id = _profile_id(config["target_context_tokens"])
    source_profile = load_soak_profile(
        profiles_file,
        source_profile_id,
    )
    target_profile = load_soak_profile(
        profiles_file,
        target_profile_id,
    )
    if int(source_profile["target_context_tokens"]) != int(
        config["source_context_tokens"]
    ):
        raise ValueError("source soak profile context does not match campaign")
    if int(target_profile["target_context_tokens"]) != int(
        config["target_context_tokens"]
    ):
        raise ValueError("target soak profile context does not match campaign")

    source_policies = _matching_policies(
        matrix,
        context_tokens=config["source_context_tokens"],
        nodes=set(config["nodes"]),
        speculations=set(config["speculations"]),
    )
    target_policies = _matching_policies(
        matrix,
        context_tokens=config["target_context_tokens"],
        nodes=set(config["nodes"]),
        speculations=set(config["speculations"]),
    )
    if not source_policies:
        raise ValueError("campaign matched no source-context policies")
    if not target_policies:
        raise ValueError("campaign matched no target-context policies")

    target_by_signature: dict[tuple[str, ...], dict[str, Any]] = {}
    for target in target_policies:
        signature = _policy_signature(target)
        if signature in target_by_signature:
            raise ValueError(
                "duplicate target policy signature: "
                + json.dumps(
                    _signature_dict(target),
                    sort_keys=True,
                )
            )
        target_by_signature[signature] = target

    candidates: list[dict[str, Any]] = []
    for policy in sorted(
        source_policies,
        key=lambda row: str(row["policy_id"]),
    ):
        signature = _policy_signature(policy)
        target = target_by_signature.get(signature)
        if target is None:
            raise ValueError(
                f"no target-context policy matches {policy['policy_id']}"
            )
        candidate_id = "candidate-" + canonical_sha256(
            {
                "campaign": config["config_sha256"],
                "source_policy_sha256": policy["policy_sha256"],
                "target_policy_sha256": target["policy_sha256"],
            }
        )[:18]
        runs = []
        for mode in MODES:
            run_id = f"{candidate_id}-{mode.replace('_prefix', '')}"
            runs.append(
                {
                    "run_id": run_id,
                    "mode": mode,
                    "profile_id": source_profile["profile_id"],
                    "profile_sha256": source_profile["profile_sha256"],
                    "policy_id": policy["policy_id"],
                    "turns": config["turns"],
                    "fresh_runtime_required": True,
                    "summary_file": (
                        f"{candidate_id}.{mode}.summary.json"
                    ),
                    "results_file": (
                        f"{candidate_id}.{mode}.results.jsonl"
                    ),
                    "plan_file": f"{candidate_id}.{mode}.plan.json",
                    "checkpoint_dir": f"{candidate_id}.checkpoints",
                }
            )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "signature": _signature_dict(policy),
                "source_policy_id": policy["policy_id"],
                "source_policy_sha256": policy["policy_sha256"],
                "source_profile_id": source_profile["profile_id"],
                "source_profile_sha256": source_profile["profile_sha256"],
                "target_policy_id": target["policy_id"],
                "target_policy_sha256": target["policy_sha256"],
                "target_profile_id": target_profile["profile_id"],
                "target_profile_sha256": target_profile["profile_sha256"],
                "source_context_tokens": config[
                    "source_context_tokens"
                ],
                "target_context_tokens": config[
                    "target_context_tokens"
                ],
                "runs": runs,
            }
        )

    plan = {
        "schema": CAMPAIGN_PLAN_SCHEMA,
        "campaign_id": config["campaign_id"],
        "config_sha256": config["config_sha256"],
        "profiles_file": profiles_file,
        "matrix_file": matrix_file,
        "source_profile_id": source_profile["profile_id"],
        "source_profile_sha256": source_profile["profile_sha256"],
        "target_profile_id": target_profile["profile_id"],
        "target_profile_sha256": target_profile["profile_sha256"],
        "turns": config["turns"],
        "requirements": {
            "fresh_process_pair": config[
                "require_fresh_process_pair"
            ],
            "same_runtime_revision": config[
                "require_same_runtime_revision"
            ],
            "same_launch_config": config[
                "require_same_launch_config"
            ],
            "source_context_tokens": config[
                "source_context_tokens"
            ],
            "target_context_tokens": config[
                "target_context_tokens"
            ],
        },
        "candidates": candidates,
        "authority": dict(DEFAULT_AUTHORITY),
        "routing_authority_changed": False,
    }
    plan["plan_sha256"] = canonical_sha256(plan)
    return plan


def validate_campaign_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("campaign plan must be an object")
    if plan.get("schema") != CAMPAIGN_PLAN_SCHEMA:
        raise ValueError("campaign plan schema mismatch")
    observed_sha = str(plan.get("plan_sha256") or "")
    if not observed_sha:
        raise ValueError("campaign plan_sha256 is required")
    unsigned = {
        key: value
        for key, value in plan.items()
        if key != "plan_sha256"
    }
    expected_sha = canonical_sha256(unsigned)
    if observed_sha != expected_sha:
        raise ValueError("campaign plan hash mismatch")
    if plan.get("routing_authority_changed") is not False:
        raise ValueError("campaign plan cannot change routing authority")
    authority = plan.get("authority")
    if not isinstance(authority, dict):
        raise ValueError("campaign plan authority must be an object")
    if any(bool(authority.get(key, False)) for key in DEFAULT_AUTHORITY):
        raise ValueError("campaign plan authority must remain all-false")
    return plan


def evaluate_campaign(
    plan: dict[str, Any],
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    plan = validate_campaign_plan(plan)
    by_key = {
        (
            str(summary.get("policy_id") or ""),
            str(summary.get("mode") or ""),
        ): summary
        for summary in summaries
        if isinstance(summary, dict)
    }

    candidate_evidence: list[dict[str, Any]] = []
    eligible_target_policies: list[dict[str, Any]] = []
    for candidate in plan.get("candidates") or []:
        stable = by_key.get(
            (candidate["source_policy_id"], "stable_prefix")
        )
        growing = by_key.get(
            (candidate["source_policy_id"], "growing_prefix")
        )
        checks = _candidate_checks(
            plan,
            candidate,
            stable,
            growing,
        )
        eligible = all(
            value.get("passed") is True
            for value in checks.values()
        )
        evidence = {
            "candidate_id": candidate["candidate_id"],
            "signature": candidate["signature"],
            "source_policy_id": candidate["source_policy_id"],
            "target_policy_id": candidate["target_policy_id"],
            "eligible_for_target_context_experiment": eligible,
            "checks": checks,
            "stable_summary_sha256": (
                canonical_sha256(stable) if stable else None
            ),
            "growing_summary_sha256": (
                canonical_sha256(growing) if growing else None
            ),
        }
        candidate_evidence.append(evidence)
        if eligible:
            eligible_target_policies.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "target_policy_id": candidate["target_policy_id"],
                    "target_policy_sha256": candidate[
                        "target_policy_sha256"
                    ],
                    "target_profile_id": candidate["target_profile_id"],
                    "target_profile_sha256": candidate[
                        "target_profile_sha256"
                    ],
                    "target_context_tokens": candidate[
                        "target_context_tokens"
                    ],
                }
            )

    return {
        "schema": CAMPAIGN_EVIDENCE_SCHEMA,
        "campaign_id": plan["campaign_id"],
        "plan_sha256": plan["plan_sha256"],
        "evaluated_candidates": candidate_evidence,
        "eligible_target_context_policies": eligible_target_policies,
        "production_promotion_authorized": False,
        "routing_authority_changed": False,
        "authority": dict(DEFAULT_AUTHORITY),
        "note": (
            "Eligibility advances only to the next benchmark context. "
            "It does not authorize production routing or runtime admission."
        ),
    }


def load_summaries(paths: list[str | Path]) -> list[dict[str, Any]]:
    result = []
    for path in paths:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected object")
        if value.get("schema") != SOAK_SUMMARY_SCHEMA:
            raise ValueError(f"{path}: unexpected summary schema")
        result.append(value)
    return result


def _candidate_checks(
    plan: dict[str, Any],
    candidate: dict[str, Any],
    stable: dict[str, Any] | None,
    growing: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    requirements = plan["requirements"]
    checks: dict[str, dict[str, Any]] = {
        "stable_present": _bool_check(stable is not None),
        "growing_present": _bool_check(growing is not None),
    }
    if stable is None or growing is None:
        return checks

    for name, summary, expected_mode in (
        ("stable", stable, "stable_prefix"),
        ("growing", growing, "growing_prefix"),
    ):
        checks[f"{name}_schema"] = _bool_check(
            summary.get("schema") == SOAK_SUMMARY_SCHEMA
        )
        checks[f"{name}_mode"] = _value_check(
            summary.get("mode"),
            expected_mode,
        )
        checks[f"{name}_profile_id"] = _value_check(
            summary.get("profile_id"),
            candidate["source_profile_id"],
        )
        checks[f"{name}_profile_sha256"] = _value_check(
            summary.get("profile_sha256"),
            candidate["source_profile_sha256"],
        )
        checks[f"{name}_policy_id"] = _value_check(
            summary.get("policy_id"),
            candidate["source_policy_id"],
        )
        checks[f"{name}_policy_sha256"] = _value_check(
            summary.get("policy_sha256"),
            candidate["source_policy_sha256"],
        )
        checks[f"{name}_context"] = _value_check(
            summary.get("target_context_tokens"),
            candidate["source_context_tokens"],
        )
        checks[f"{name}_turns_complete"] = _bool_check(
            summary.get("completed_turns")
            == summary.get("expected_turns")
            == plan["turns"]
        )
        checks[f"{name}_passed"] = _bool_check(
            summary.get("passed") is True
        )
        identity = summary.get("runtime_identity")
        checks[f"{name}_runtime_identity_consistent"] = _bool_check(
            isinstance(identity, dict)
            and identity.get("consistent") is True
            and bool(identity.get("runtime_revision"))
            and bool(identity.get("launch_config_sha256"))
            and identity.get("process_started_at_unix_ms")
            is not None
        )

    stable_identity = stable.get("runtime_identity") or {}
    growing_identity = growing.get("runtime_identity") or {}

    if requirements.get("same_runtime_revision"):
        checks["same_runtime_revision"] = _value_check(
            stable_identity.get("runtime_revision"),
            growing_identity.get("runtime_revision"),
        )
    if requirements.get("same_launch_config"):
        checks["same_launch_config"] = _value_check(
            stable_identity.get("launch_config_sha256"),
            growing_identity.get("launch_config_sha256"),
        )
    if requirements.get("fresh_process_pair"):
        stable_started = stable_identity.get(
            "process_started_at_unix_ms"
        )
        growing_started = growing_identity.get(
            "process_started_at_unix_ms"
        )
        checks["fresh_process_pair"] = {
            "passed": (
                stable_started is not None
                and growing_started is not None
                and stable_started != growing_started
            ),
            "stable_process_started_at_unix_ms": stable_started,
            "growing_process_started_at_unix_ms": growing_started,
        }
    return checks


def _matching_policies(
    matrix: dict[str, Any],
    *,
    context_tokens: int,
    nodes: set[str],
    speculations: set[str],
) -> list[dict[str, Any]]:
    return [
        policy
        for policy in matrix.get("policies") or []
        if policy.get("enabled", True)
        and int(policy.get("context_tokens") or 0) == context_tokens
        and str(policy.get("node_id") or "") in nodes
        and str(policy.get("speculation") or "") in speculations
        and int(policy.get("concurrency") or 0) == 1
        and policy.get("telemetry_required") is True
    ]


def _policy_signature(policy: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(policy.get("node_id") or ""),
        str(policy.get("backend") or ""),
        str(policy.get("quantization") or ""),
        str(policy.get("speculation") or ""),
        str(policy.get("model_handle") or ""),
    )


def _signature_dict(policy: dict[str, Any]) -> dict[str, str]:
    keys = (
        "node_id",
        "backend",
        "quantization",
        "speculation",
        "model_handle",
    )
    return {key: str(policy.get(key) or "") for key in keys}


def _profile_id(context_tokens: int) -> str:
    if context_tokens == 32768:
        return "assistx-32k-soak-v1"
    if context_tokens == 131072:
        return "assistx-128k-soak-v1"
    return f"assistx-{context_tokens}-soak-v1"


def _bool_check(passed: bool) -> dict[str, Any]:
    return {"passed": bool(passed)}


def _value_check(
    observed: Any,
    expected: Any,
) -> dict[str, Any]:
    return {
        "passed": observed == expected,
        "observed": observed,
        "expected": expected,
    }
