from __future__ import annotations

import hashlib
import hmac
import json
import posixpath
from typing import Any

from .improvement_runtime import verify_executor_evidence

TIER_LIMITS = {
    "tool-small": {"max_files": 2, "max_diff_lines": 160},
    "reasoning-mid": {"max_files": 5, "max_diff_lines": 500},
    "reasoning-large": {"max_files": 10, "max_diff_lines": 1200},
}
TIER_ORDER = ["tool-small", "reasoning-mid", "reasoning-large"]
ALLOWED_VERIFICATION_EXECUTABLES = {
    "pytest",
    "python",
    "python3",
    "ruff",
    "mypy",
    "npm",
    "pnpm",
    "yarn",
    "node",
    "cargo",
    "go",
}
DEFAULT_TOOLS = [
    "inspect_file",
    "apply_patch",
    "run_verification",
    "inspect_diff",
]


def _payload(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("payload") or task.get("payload_json") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
    return value if isinstance(value, dict) else {}


def _safe_relative_path(value: str) -> str:
    normalized = posixpath.normpath(str(value).replace("\\", "/")).lstrip("/")
    if normalized in {"", "."} or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"unsafe repository-relative path: {value}")
    return normalized


def _verification_command(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 20:
        raise ValueError("verification commands must be non-empty argv arrays")
    argv = [str(part) for part in value]
    executable = posixpath.basename(argv[0])
    if executable not in ALLOWED_VERIFICATION_EXECUTABLES:
        raise ValueError(f"verification executable is not allowed: {executable}")
    if any("\n" in part or "\x00" in part for part in argv):
        raise ValueError("verification arguments contain unsafe control characters")
    return argv


def build_execution_contract(
    *,
    repository: str,
    objective: str,
    allowed_paths: list[str],
    verification_commands: list[list[str]],
    recommended_tier: str = "tool-small",
    iteration: int = 0,
) -> dict[str, Any]:
    if recommended_tier not in TIER_LIMITS:
        raise ValueError(f"unsupported improvement tier: {recommended_tier}")
    repository = repository.strip()
    objective = objective.strip()
    if not repository:
        raise ValueError("repository is required")
    if not objective:
        raise ValueError("objective is required")
    paths = list(dict.fromkeys(_safe_relative_path(path) for path in allowed_paths))
    if not paths:
        raise ValueError("at least one allowed path is required")
    limits = TIER_LIMITS[recommended_tier]
    if len(paths) > limits["max_files"]:
        raise ValueError(
            f"{recommended_tier} tasks may touch at most {limits['max_files']} files"
        )
    commands = [_verification_command(command) for command in verification_commands]
    if not commands:
        raise ValueError("at least one verification command is required")
    return {
        "version": 2,
        "kind": "bounded_code_change",
        "repository": repository,
        "objective": objective,
        "allowed_paths": paths,
        "allowed_tools": list(DEFAULT_TOOLS),
        "verification_commands": commands,
        "recommended_tier": recommended_tier,
        "max_files": limits["max_files"],
        "max_diff_lines": limits["max_diff_lines"],
        "iteration": max(0, int(iteration)),
        "max_iterations": 2,
        "requires_clean_worktree": True,
        "requires_isolated_worktree": True,
        "requires_signed_attestation": True,
        "max_patch_bytes": 524288,
        "requires_review": True,
    }


def task_contract(task: dict[str, Any]) -> dict[str, Any] | None:
    contract = _payload(task).get("execution_contract")
    if not isinstance(contract, dict) or contract.get("kind") != "bounded_code_change":
        return None
    return contract


def build_work_packet(
    task: dict[str, Any],
    *,
    learned_lessons: list[str] | None = None,
) -> dict[str, Any] | None:
    contract = task_contract(task)
    if not contract:
        return None
    return {
        "objective": contract.get("objective") or task.get("title"),
        "repository": contract.get("repository"),
        "scope": {
            "allowed_paths": contract.get("allowed_paths", []),
            "max_files": contract.get("max_files"),
            "max_diff_lines": contract.get("max_diff_lines"),
            "forbidden": [
                "touching files outside allowed_paths",
                "dependency or lockfile changes unless explicitly allowed",
                "network access",
                "commits, pushes, or pull requests",
                "claiming success without verification evidence",
            ],
        },
        "tool_recipe": [
            {
                "step": 1,
                "tool": "inspect_file",
                "instruction": "Read only the allowed files and identify the smallest edit.",
            },
            {
                "step": 2,
                "tool": "apply_patch",
                "instruction": "Apply one bounded patch inside allowed_paths.",
            },
            {
                "step": 3,
                "tool": "run_verification",
                "commands": contract.get("verification_commands", []),
            },
            {
                "step": 4,
                "tool": "inspect_diff",
                "instruction": "Report exact changed files and added plus deleted lines.",
            },
        ],
        "learned_lessons": (learned_lessons or [])[-5:],
        "completion_envelope": {
            "changed_files": ["repository/relative/path"],
            "diff_lines": 0,
            "tools_used": list(DEFAULT_TOOLS),
            "verification": [
                {"command": ["pytest", "..."], "returncode": 0}
            ],
            "summary": "brief factual summary",
            "next_candidate": "optional next bounded improvement",
        },
    }


def extract_completion_envelope(output: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, character in enumerate(output or ""):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and (
            "changed_files" in value or "completion_envelope" in value
        ):
            candidates.append(value)
    if not candidates:
        return None
    value = candidates[-1]
    nested = value.get("completion_envelope")
    return nested if isinstance(nested, dict) else value


def evaluate_completion(
    task: dict[str, Any],
    *,
    requested_status: str,
    result: dict[str, Any] | None,
    verify_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    contract = task_contract(task)
    if not contract:
        return {
            "managed": False,
            "accepted": requested_status == "DONE",
            "effective_status": requested_status,
            "reasons": [],
        }
    evidence = (result or {}).get("completion_envelope") or (result or {}).get(
        "change_evidence"
    )
    reasons: list[str] = []
    if requested_status != "DONE":
        reasons.append(f"agent_reported_{requested_status.lower()}")
    if not isinstance(evidence, dict):
        reasons.append("missing_completion_envelope")
        evidence = {}
    if evidence.get("evidence_source") != "executor":
        reasons.append("unattested_model_evidence")
    attested, attestation_reason = verify_executor_evidence(
        evidence, verify_keys=verify_keys
    )
    if not attested:
        reasons.append(attestation_reason)
    if not evidence.get("worktree_clean_before"):
        reasons.append("worktree_not_clean_before_execution")
    if evidence.get("isolated_worktree") is not True:
        reasons.append("execution_not_isolated")
    if evidence.get("scope_validated") is not True:
        reasons.append("scope_not_executor_validated")
    patch = evidence.get("patch")
    if not isinstance(patch, str) or not patch:
        reasons.append("missing_patch_artifact")
    patch_fingerprint = str(evidence.get("patch_sha256") or "")
    if not patch_fingerprint:
        reasons.append("missing_patch_fingerprint")
    elif isinstance(patch, str) and patch and not hmac.compare_digest(
        hashlib.sha256(patch.encode()).hexdigest(),
        patch_fingerprint,
    ):
        reasons.append("patch_fingerprint_invalid")

    allowed_paths = set(contract.get("allowed_paths") or [])
    changed_files = evidence.get("changed_files")
    if not isinstance(changed_files, list) or not changed_files:
        reasons.append("missing_changed_files")
        changed_files = []
    normalized_files = []
    for path in changed_files:
        try:
            normalized_files.append(_safe_relative_path(str(path)))
        except ValueError:
            reasons.append("unsafe_changed_path")
    if any(path not in allowed_paths for path in normalized_files):
        reasons.append("changed_file_outside_contract")
    if len(set(normalized_files)) > int(contract.get("max_files") or 0):
        reasons.append("file_budget_exceeded")

    try:
        diff_lines = int(evidence.get("diff_lines"))
    except (TypeError, ValueError):
        diff_lines = -1
    if diff_lines < 1:
        reasons.append("missing_or_empty_diff")
    elif diff_lines > int(contract.get("max_diff_lines") or 0):
        reasons.append("diff_budget_exceeded")

    tools_used = evidence.get("tools_used")
    allowed_tools = set(contract.get("allowed_tools") or [])
    if not isinstance(tools_used, list):
        reasons.append("missing_tool_trace")
        tools_used = []
    elif any(tool not in allowed_tools for tool in tools_used):
        reasons.append("tool_outside_contract")
    for required_tool in DEFAULT_TOOLS:
        if required_tool not in tools_used:
            reasons.append(f"missing_tool_{required_tool}")

    required_commands = contract.get("verification_commands") or []
    verification = evidence.get("verification")
    observed: dict[str, int] = {}
    if isinstance(verification, list):
        for item in verification:
            if not isinstance(item, dict) or not isinstance(item.get("command"), list):
                continue
            observed[json.dumps(item["command"])] = int(item.get("returncode", -1))
    else:
        reasons.append("missing_verification")
    for command in required_commands:
        returncode = observed.get(json.dumps(command))
        if returncode is None:
            reasons.append(f"verification_not_run:{' '.join(command)}")
        elif returncode != 0:
            reasons.append(f"verification_failed:{' '.join(command)}")

    reasons = list(dict.fromkeys(reasons))
    accepted = not reasons
    return {
        "managed": True,
        "accepted": accepted,
        "effective_status": "DONE" if accepted else "FAILED",
        "reasons": reasons,
        "evidence": evidence,
        "contract": contract,
    }


def next_tier(tier: str) -> str:
    try:
        return TIER_ORDER[min(TIER_ORDER.index(tier) + 1, len(TIER_ORDER) - 1)]
    except ValueError:
        return "reasoning-mid"


class ImprovementCycle:
    def record_attempt(
        self,
        neo: Any,
        task: dict[str, Any],
        *,
        agent_id: str,
        model_id: str | None,
        evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        contract = evaluation["contract"]
        family = str(task.get("kind") or "bounded_code_change")
        profile_key = f"{agent_id}:{model_id or 'unknown'}:{family}"
        attempt_id = (
            f"improvement-attempt:{task.get('id')}:"
            f"{task.get('execution_attempt', 0)}"
        )
        with neo._session() as session:
            row = session.run(
                """
                MATCH (task:Task {id:$task_id})
                MERGE (attempt:ImprovementAttempt {id:$attempt_id})
                ON CREATE SET attempt.created_at_ts=timestamp(),
                              attempt.counted=false
                WITH task, attempt, coalesce(attempt.counted, false) AS counted
                SET attempt.agent_id=$agent_id,
                    attempt.model_id=$model_id,
                    attempt.task_family=$family,
                    attempt.accepted=$accepted,
                    attempt.reasons_json=$reasons_json,
                    attempt.evidence_json=$evidence_json,
                    attempt.contract_json=$contract_json,
                    attempt.counted=true,
                    attempt.updated_at_ts=timestamp()
                MERGE (task)-[:HAS_IMPROVEMENT_ATTEMPT]->(attempt)
                MERGE (profile:AgentSkillProfile {profile_key:$profile_key})
                ON CREATE SET profile.created_at_ts=timestamp(),
                              profile.attempts=0,
                              profile.verified_successes=0
                SET profile.agent_id=$agent_id,
                    profile.model_id=$model_id,
                    profile.task_family=$family,
                    profile.attempts=profile.attempts +
                      CASE WHEN counted THEN 0 ELSE 1 END,
                    profile.verified_successes=profile.verified_successes +
                      CASE WHEN NOT counted AND $accepted THEN 1 ELSE 0 END,
                    profile.last_reasons_json=$reasons_json,
                    profile.last_task_id=$task_id,
                    profile.updated_at_ts=timestamp()
                SET profile.verified_rate =
                  toFloat(profile.verified_successes) / profile.attempts
                MERGE (attempt)-[:UPDATES_SKILL_PROFILE]->(profile)
                RETURN attempt, profile
                """,
                {
                    "task_id": task["id"],
                    "attempt_id": attempt_id,
                    "agent_id": agent_id,
                    "model_id": model_id,
                    "family": family,
                    "accepted": bool(evaluation["accepted"]),
                    "reasons_json": json.dumps(evaluation["reasons"], sort_keys=True),
                    "evidence_json": json.dumps(
                        evaluation.get("evidence") or {}, default=str, sort_keys=True
                    ),
                    "contract_json": json.dumps(contract, sort_keys=True),
                    "profile_key": profile_key,
                },
            ).single()
        return {
            "attempt": dict(row["attempt"]) if row else {},
            "profile": dict(row["profile"]) if row else {},
        }

    def propose_repair(
        self,
        neo: Any,
        task: dict[str, Any],
        evaluation: dict[str, Any],
    ) -> str | None:
        if evaluation["accepted"]:
            return None
        contract = dict(evaluation["contract"])
        iteration = int(contract.get("iteration") or 0)
        if iteration >= int(contract.get("max_iterations") or 2):
            return None
        recommended_tier = next_tier(str(contract.get("recommended_tier")))
        narrowed_paths = list(contract.get("allowed_paths") or [])[:1]
        repair_contract = build_execution_contract(
            repository=str(contract.get("repository") or ""),
            objective=(
                f"Repair the failed bounded change for {task.get('title')}. "
                f"Observed failures: {', '.join(evaluation['reasons'])}"
            ),
            allowed_paths=narrowed_paths,
            verification_commands=contract.get("verification_commands") or [],
            recommended_tier=recommended_tier,
            iteration=iteration + 1,
        )
        return neo.upsert_ticket(
            title=f"Repair: {task.get('title')}",
            ticket_type="improvement_repair",
            status="PROPOSED",
            kind="improvement_repair",
            parent_id=task["id"],
            required_capabilities=["llm", "code_execution"],
            priority=task.get("priority") or "MEDIUM",
            payload={
                "execution_contract": repair_contract,
                "learned_from_task_id": task["id"],
                "learned_failure_reasons": evaluation["reasons"],
                "requires_approval": True,
            },
            idempotency_key=f"improvement-repair:{task['id']}:{iteration + 1}",
            preemptible=True,
            max_migrations=2,
        )

    def status(self, neo: Any, limit: int = 100) -> dict[str, Any]:
        with neo._session() as session:
            profiles = [
                dict(row["profile"])
                for row in session.run(
                    """
                    MATCH (profile:AgentSkillProfile)
                    RETURN profile
                    ORDER BY profile.updated_at_ts DESC
                    LIMIT $limit
                    """,
                    {"limit": max(1, min(limit, 500))},
                )
            ]
            attempts = []
            for row in session.run(
                """
                MATCH (attempt:ImprovementAttempt)
                RETURN attempt
                ORDER BY attempt.updated_at_ts DESC
                LIMIT $limit
                """,
                {"limit": max(1, min(limit, 500))},
            ):
                attempt = dict(row["attempt"])
                try:
                    evidence = json.loads(attempt.pop("evidence_json", "{}"))
                except json.JSONDecodeError:
                    evidence = {}
                attempt["patch_sha256"] = evidence.get("patch_sha256")
                attempt["changed_files"] = evidence.get("changed_files") or []
                attempt["summary"] = evidence.get("summary")
                attempt["executor_id"] = evidence.get("executor_id")
                attempts.append(attempt)
        return {
            "profiles": profiles,
            "attempts": attempts,
            "summary": {
                "profiles": len(profiles),
                "attempts": len(attempts),
                "verified": sum(1 for attempt in attempts if attempt.get("accepted")),
            },
        }

    def get_attempt(self, neo: Any, attempt_id: str) -> dict[str, Any] | None:
        with neo._session() as session:
            row = session.run(
                """
                MATCH (task:Task)-[:HAS_IMPROVEMENT_ATTEMPT]->(
                  attempt:ImprovementAttempt {id:$attempt_id}
                )
                RETURN task, attempt
                """,
                {"attempt_id": attempt_id},
            ).single()
        if not row:
            return None
        attempt = dict(row["attempt"])
        try:
            evidence = json.loads(attempt.get("evidence_json") or "{}")
            contract = json.loads(attempt.get("contract_json") or "{}")
        except json.JSONDecodeError:
            evidence, contract = {}, {}
        return {
            "task": dict(row["task"]),
            "attempt": attempt,
            "evidence": evidence,
            "contract": contract,
        }

    def record_promotion(
        self,
        neo: Any,
        attempt_id: str,
        *,
        actor: str,
        reason: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with neo._session() as session:
            row = session.run(
                """
                MATCH (attempt:ImprovementAttempt {id:$attempt_id})
                SET attempt.promotion_status=$status,
                    attempt.promotion_actor=$actor,
                    attempt.promotion_reason=$reason,
                    attempt.promotion_result_json=$result_json,
                    attempt.promoted_at_ts=CASE
                      WHEN $promoted THEN timestamp()
                      ELSE attempt.promoted_at_ts
                    END,
                    attempt.updated_at_ts=timestamp()
                RETURN attempt
                """,
                {
                    "attempt_id": attempt_id,
                    "status": "PROMOTED" if result.get("promoted") else "REJECTED",
                    "actor": actor,
                    "reason": reason,
                    "result_json": json.dumps(result, default=str, sort_keys=True),
                    "promoted": bool(result.get("promoted")),
                },
            ).single()
        return dict(row["attempt"]) if row else {}

    def claim_promotion(
        self,
        neo: Any,
        attempt_id: str,
        *,
        actor: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        with neo._session() as session:
            row = session.run(
                """
                MATCH (attempt:ImprovementAttempt {id:$attempt_id})
                WHERE attempt.accepted=true
                  AND coalesce(attempt.promotion_status, '') <> 'PROMOTED'
                  AND (
                    coalesce(attempt.promotion_status, '') <> 'PROMOTING'
                    OR coalesce(attempt.promotion_started_at_ts, 0)
                      < timestamp() - 600000
                  )
                SET attempt.promotion_status='PROMOTING',
                    attempt.promotion_actor=$actor,
                    attempt.promotion_fingerprint=$fingerprint,
                    attempt.promotion_started_at_ts=timestamp(),
                    attempt.updated_at_ts=timestamp()
                RETURN attempt
                """,
                {
                    "attempt_id": attempt_id,
                    "actor": actor,
                    "fingerprint": fingerprint,
                },
            ).single()
        return dict(row["attempt"]) if row else None
