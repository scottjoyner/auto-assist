import hashlib
import json

import pytest

from assistx.improvement_cycle import (
    ImprovementCycle,
    build_execution_contract,
    build_work_packet,
    evaluate_completion,
    extract_completion_envelope,
)
from assistx.improvement_runtime import sign_executor_evidence


def contract(**overrides):
    values = {
        "repository": "auto-assist",
        "objective": "Add one focused regression test",
        "allowed_paths": ["tests/test_example.py"],
        "verification_commands": [["pytest", "-q", "tests/test_example.py"]],
        "recommended_tier": "tool-small",
    }
    values.update(overrides)
    return build_execution_contract(**values)


def task_with_contract(value=None):
    return {
        "id": "task-1",
        "title": "Bounded test improvement",
        "kind": "bounded_code_change",
        "payload_json": json.dumps({"execution_contract": value or contract()}),
    }


def valid_envelope():
    patch = "diff --git a/tests/test_example.py b/tests/test_example.py\n"
    return sign_executor_evidence({
        "evidence_source": "executor",
        "executor_id": "small-agent",
        "worktree_clean_before": True,
        "isolated_worktree": True,
        "scope_validated": True,
        "changed_files": ["tests/test_example.py"],
        "diff_lines": 20,
        "tools_used": [
            "inspect_file",
            "apply_patch",
            "run_verification",
            "inspect_diff",
        ],
        "verification": [
            {
                "command": ["pytest", "-q", "tests/test_example.py"],
                "returncode": 0,
            }
        ],
        "summary": "Added the bounded regression test.",
        "patch": patch,
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
    }, key_id="node-v1", secret="node-secret")


def test_contract_rejects_scope_escape_and_shell_commands():
    with pytest.raises(ValueError, match="unsafe repository-relative path"):
        contract(allowed_paths=["../outside.py"])
    with pytest.raises(ValueError, match="executable is not allowed"):
        contract(verification_commands=[["bash", "-c", "anything"]])
    with pytest.raises(ValueError, match="repository is required"):
        contract(repository=" ")
    with pytest.raises(ValueError, match="objective is required"):
        contract(objective="\n")


def test_small_agent_work_packet_is_deterministic_and_bounded():
    packet = build_work_packet(task_with_contract())

    assert packet["scope"]["max_files"] == 2
    assert packet["scope"]["max_diff_lines"] == 160
    assert [step["tool"] for step in packet["tool_recipe"]] == [
        "inspect_file",
        "apply_patch",
        "run_verification",
        "inspect_diff",
    ]


def test_verified_completion_is_accepted():
    result = evaluate_completion(
        task_with_contract(),
        requested_status="DONE",
        result={"completion_envelope": valid_envelope()},
        verify_keys={"node-v1": "node-secret"},
    )

    assert result["managed"] is True
    assert result["accepted"] is True
    assert result["effective_status"] == "DONE"
    assert result["reasons"] == []


def test_prose_only_or_scope_escaping_completion_is_rejected():
    missing = evaluate_completion(
        task_with_contract(),
        requested_status="DONE",
        result={"output": "Done, everything is fixed."},
    )
    escaped_envelope = valid_envelope()
    escaped_envelope["changed_files"] = ["src/production.py"]
    escaped = evaluate_completion(
        task_with_contract(),
        requested_status="DONE",
        result={"completion_envelope": escaped_envelope},
        verify_keys={"node-v1": "node-secret"},
    )

    assert missing["effective_status"] == "FAILED"
    assert "missing_completion_envelope" in missing["reasons"]
    assert escaped["effective_status"] == "FAILED"
    assert "changed_file_outside_contract" in escaped["reasons"]


def test_unsigned_executor_evidence_is_rejected():
    envelope = valid_envelope()
    envelope.pop("attestation")

    result = evaluate_completion(
        task_with_contract(),
        requested_status="DONE",
        result={"completion_envelope": envelope},
        verify_keys={"node-v1": "node-secret"},
    )

    assert result["accepted"] is False
    assert "missing_executor_attestation" in result["reasons"]


def test_completion_envelope_can_be_extracted_from_model_output():
    output = f"```json\n{json.dumps(valid_envelope())}\n```"

    assert extract_completion_envelope(output)["diff_lines"] == 20


class RepairNeo:
    def __init__(self):
        self.kwargs = None

    def upsert_ticket(self, **kwargs):
        self.kwargs = kwargs
        return "repair-task-1"


def test_failed_attempt_creates_narrower_review_first_repair():
    original = task_with_contract(
        contract(
            recommended_tier="reasoning-mid",
            allowed_paths=["src/a.py", "tests/test_a.py"],
        )
    )
    evaluation = evaluate_completion(
        original,
        requested_status="FAILED",
        result={},
    )
    neo = RepairNeo()

    repair_id = ImprovementCycle().propose_repair(neo, original, evaluation)

    assert repair_id == "repair-task-1"
    assert neo.kwargs["status"] == "PROPOSED"
    repair_contract = neo.kwargs["payload"]["execution_contract"]
    assert repair_contract["allowed_paths"] == ["src/a.py"]
    assert repair_contract["recommended_tier"] == "reasoning-large"
    assert repair_contract["iteration"] == 1


def test_completion_api_downgrades_unverified_done_to_failed(monkeypatch):
    from assistx import api

    managed_task = {
        **task_with_contract(),
        "status": "CLAIMED",
        "claimed_by": "small-agent",
        "claim_id": "claim-1",
        "execution_attempt": 1,
    }

    class CompletionNeo:
        def __init__(self):
            self.completion = None

        def get_task(self, _task_id):
            return managed_task

        def complete_task(self, **kwargs):
            self.completion = kwargs
            return {**managed_task, "status": kwargs["status"]}

        @staticmethod
        def _with_retry(operation):
            return operation()

    neo = CompletionNeo()
    monkeypatch.setattr(api, "_neo_fleet", lambda: neo)
    monkeypatch.setattr(
        api,
        "_maybe_dead_letter_exhausted_task",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        api._improvement_cycle,
        "record_attempt",
        lambda *_args, **_kwargs: {"profile": {"attempts": 1}},
    )
    monkeypatch.setattr(
        api._improvement_cycle,
        "propose_repair",
        lambda *_args, **_kwargs: "repair-1",
    )

    response = api.api_complete_task(
        "task-1",
        api.TaskCompleteIn(
            agent_id="small-agent",
            claim_id="claim-1",
            status="DONE",
            result={"output": "Done."},
        ),
        None,
        "operator",
    )

    assert neo.completion["status"] == "FAILED"
    assert response["task"]["status"] == "FAILED"
    assert response["improvement_outcome"]["accepted"] is False
    assert response["improvement_outcome"]["repair_task_id"] == "repair-1"


def test_promotion_api_requires_exact_fingerprint_and_records_operator(monkeypatch):
    from assistx import api

    fingerprint = "b" * 64

    class PromotionNeo:
        closed = False

        def close(self):
            self.closed = True

    neo = PromotionNeo()
    record = {
        "task": task_with_contract(),
        "attempt": {"id": "attempt-1", "accepted": True},
        "contract": contract(),
        "evidence": {"patch_sha256": fingerprint},
    }
    captured = {}
    monkeypatch.setattr(api, "_neo", lambda: neo)
    monkeypatch.setattr(
        api._improvement_cycle,
        "get_attempt",
        lambda *_args: record,
    )
    monkeypatch.setattr(
        api._improvement_cycle,
        "claim_promotion",
        lambda *_args, **_kwargs: {"promotion_status": "PROMOTING"},
    )
    monkeypatch.setattr(
        api,
        "promote_patch",
        lambda *_args, **kwargs: {
            "promoted": kwargs["expected_fingerprint"] == fingerprint,
            "patch_sha256": fingerprint,
        },
    )

    def record_promotion(*_args, **kwargs):
        captured.update(kwargs)
        return {"promotion_status": "PROMOTED"}

    monkeypatch.setattr(
        api._improvement_cycle,
        "record_promotion",
        record_promotion,
    )

    response = api.api_promote_improvement_patch(
        "attempt-1",
        api.PatchPromotionIn(
            fingerprint=fingerprint,
            reason="Reviewed exact patch and checks.",
        ),
        "operator",
    )

    assert response["promoted"] is True
    assert response["promotion_status"] == "PROMOTED"
    assert captured["actor"] == "operator"
    assert captured["reason"] == "Reviewed exact patch and checks."
    assert neo.closed is True
