"""Hermetic fence for malformed verification return codes in managed completion.

All evidence is synthetic and HMAC-signed in-process with a test-only secret.
No Neo4j container, HTTP endpoint, Redis, SQLite DB, credential file, recovery-hold
file, docker daemon, or network mount is read. The repository-level ``conftest.py``
Neo4j integration fixtures are not used by these tests.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from assistx.improvement_cycle import (
    build_execution_contract,
    evaluate_completion,
)
from assistx.improvement_runtime import sign_executor_evidence

_KEY_ID = "sweep15-test-key"
_SECRET = "sweep15-test-secret-only"
_COMMAND = ["pytest", "-q", "tests/test_example.py"]


def _task() -> dict[str, object]:
    contract = build_execution_contract(
        repository="auto-assist",
        objective="Add one focused regression test",
        allowed_paths=["tests/test_example.py"],
        verification_commands=[_COMMAND],
        recommended_tier="tool-small",
    )
    return {
        "id": "task-sweep15",
        "title": "Bounded test improvement",
        "kind": "bounded_code_change",
        "payload_json": json.dumps({"execution_contract": contract}),
    }


def _envelope(returncode: object) -> dict[str, object]:
    patch = "diff --git a/tests/test_example.py b/tests/test_example.py\n"
    evidence = {
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
        "verification": [{"command": _COMMAND, "returncode": returncode}],
        "summary": "Added the bounded regression test.",
        "patch": patch,
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
    }
    return sign_executor_evidence(
        evidence, key_id=_KEY_ID, secret=_SECRET
    )


def _evaluate(returncode: object) -> dict[str, object]:
    return evaluate_completion(
        _task(),
        requested_status="DONE",
        result={"completion_envelope": _envelope(returncode)},
        verify_keys={_KEY_ID: _SECRET},
    )


@pytest.mark.parametrize(
    "malformed",
    [
        0.5,
        0.999,
        -0.25,
        False,
        " 0 ",
        "0",
    ],
)
def test_malformed_returncode_is_not_accepted_as_success(malformed: object) -> None:
    """A non-integer return code must never be coerced to exit status 0."""
    verdict = _evaluate(malformed)
    assert verdict["accepted"] is False, (
        f"malformed returncode {malformed!r} was accepted: {verdict['reasons']}"
    )
    assert verdict["effective_status"] == "FAILED"
    assert any(
        reason.startswith("verification_returncode_invalid")
        for reason in verdict["reasons"]
    ), verdict["reasons"]


@pytest.mark.parametrize("malformed", ["abc", None, [], {}])
def test_non_numeric_returncode_is_rejected_not_crashed(malformed: object) -> None:
    """Malformed evidence must fail the task, not raise out of the checker."""
    verdict = _evaluate(malformed)
    assert verdict["accepted"] is False
    assert verdict["effective_status"] == "FAILED"
    assert any(
        reason.startswith("verification_returncode_invalid")
        for reason in verdict["reasons"]
    ), verdict["reasons"]


def test_valid_returncodes_keep_existing_semantics() -> None:
    """Integer 0 still succeeds and integer 1 still fails (controls)."""
    ok = _evaluate(0)
    assert ok["accepted"] is True
    assert ok["effective_status"] == "DONE"
    assert ok["reasons"] == []

    failed = _evaluate(1)
    assert failed["accepted"] is False
    assert failed["effective_status"] == "FAILED"
    assert any(
        reason.startswith("verification_failed") for reason in failed["reasons"]
    ), failed["reasons"]
