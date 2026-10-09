import json
import subprocess
import sys
from pathlib import Path

from assistx.improvement_cycle import build_execution_contract
from assistx.improvement_runtime import (
    cleanup_worktree,
    collect_executor_evidence,
    prepare_repository,
    promote_patch,
    verify_executor_evidence,
)


def git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def initialized_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "canary@example.com")
    git(repo, "config", "user.name", "Canary")
    target = repo / "module.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", "module.py")
    git(repo, "commit", "-m", "seed")
    return repo


def contract():
    return build_execution_contract(
        repository="repo",
        objective="Change one value",
        allowed_paths=["module.py"],
        verification_commands=[
            [sys.executable, "-c", "import module; assert module.VALUE == 2"]
        ],
    )


def runtime_env(repo, tmp_path):
    return {
        "ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps({"repo": str(repo)}),
        "ASSISTX_IMPROVEMENT_WORKTREE_ROOT": str(tmp_path / "worktrees"),
        "ASSISTX_IMPROVEMENT_ATTESTATION_KEY_ID": "node-v1",
        "ASSISTX_IMPROVEMENT_ATTESTATION_SECRET": "node-secret",
    }


def build_evidence(repo, tmp_path):
    value = contract()
    env = runtime_env(repo, tmp_path)
    prepared = prepare_repository(value, task_id="task-1", env=env)
    workspace = Path(prepared["root"])
    (workspace / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    evidence = collect_executor_evidence(
        value,
        prepared,
        {
            "tools_used": [
                "inspect_file",
                "apply_patch",
                "run_verification",
                "inspect_diff",
            ],
            "summary": "Changed the value.",
        },
        executor_id="node-1",
        env=env,
    )
    return value, env, prepared, evidence


def test_workspace_source_binding_is_exact_and_mismatch_fails_closed(tmp_path):
    """Do not mistake a claimed SHA for accepted repository provenance.

    The authoritative `RepositorySourceBinding` contract now requires the
    configured alias, exact repository/worktree realpaths and a pinned HEAD.
    Earlier fixtures supplied only `head_sha` and asserted that a mismatch
    could continue. That is incompatible with the current fail-closed verifier.
    """
    repo = initialized_repo(tmp_path)
    env = runtime_env(repo, tmp_path)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    plain = prepare_repository(contract(), task_id="t-plain", env=env)
    assert plain["ok"] is True
    assert plain["head"] == head
    assert plain["source_binding_verification"] is None
    assert cleanup_worktree(plain)["cleaned"] is True

    bound = {
        "repository": "repo",
        "repository_realpath": str(repo.resolve()),
        "worktree_realpath": str(repo.resolve()),
        "head_sha": head,
        "dirty_expectation": "clean_required",
    }
    matching = dict(contract(), source_binding=bound)
    same = prepare_repository(matching, task_id="t-same", env=env)
    assert same["ok"] is True
    assert same["head"] == head
    assert same["source_binding_verification"]["state"] == "MATCH"
    assert same["source_binding_verification"]["accepted"] is True
    assert cleanup_worktree(same)["cleaned"] is True

    # A validly formed binding referring to a stale commit must be rejected
    # *before* an executor worktree can be created; it is not a warning-only
    # mismatch that could later authorize stale evidence or patch promotion.
    wrong = dict(contract(), source_binding={**bound, "head_sha": "b" * 40})
    rejected = prepare_repository(wrong, task_id="t-diff", env=env)
    assert rejected["ok"] is False
    assert rejected["reason"] == "repository_source_binding_rejected:HEAD_MISMATCH"
    assert rejected["source_binding_verification"]["accepted"] is False
    assert rejected["source_binding_verification"]["state"] == "HEAD_MISMATCH"
    assert not list((tmp_path / "worktrees").glob("*"))


def test_executor_uses_isolated_worktree_and_exports_signed_patch(tmp_path):
    repo = initialized_repo(tmp_path)
    value, _, prepared, evidence = build_evidence(repo, tmp_path)

    assert prepared["ok"] is True
    assert prepared["isolated"] is True
    assert Path(prepared["root"]) != repo
    assert (repo / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert evidence["evidence_source"] == "executor"
    assert evidence["isolated_worktree"] is True
    assert evidence["scope_validated"] is True
    assert evidence["changed_files"] == ["module.py"]
    assert evidence["diff_lines"] == 2
    assert evidence["verification"][0]["returncode"] == 0
    assert evidence["patch_sha256"]
    assert "VALUE = 2" in evidence["patch"]
    assert verify_executor_evidence(
        evidence, verify_keys={"node-v1": "node-secret"}
    ) == (True, "")

    cleaned = cleanup_worktree(prepared)
    assert cleaned["cleaned"] is True
    assert not Path(prepared["root"]).exists()
    assert value["repository"] == "repo"


def test_dirty_base_does_not_contaminate_isolated_attempt(tmp_path):
    repo = initialized_repo(tmp_path)
    (repo / "local-only.txt").write_text("operator work\n", encoding="utf-8")
    env = runtime_env(repo, tmp_path)

    prepared = prepare_repository(contract(), task_id="task-dirty", env=env)

    assert prepared["ok"] is True
    assert not (Path(prepared["root"]) / "local-only.txt").exists()
    cleanup_worktree(prepared)


def test_tampered_attestation_is_rejected(tmp_path):
    repo = initialized_repo(tmp_path)
    _, _, prepared, evidence = build_evidence(repo, tmp_path)
    evidence["changed_files"] = ["outside.py"]

    verified, reason = verify_executor_evidence(
        evidence, verify_keys={"node-v1": "node-secret"}
    )

    assert verified is False
    assert reason == "executor_attestation_invalid"
    cleanup_worktree(prepared)


def test_operator_promotion_requires_fingerprint_and_rechecks_patch(tmp_path):
    repo = initialized_repo(tmp_path)
    value, env, prepared, evidence = build_evidence(repo, tmp_path)
    cleanup_worktree(prepared)

    rejected = promote_patch(
        value,
        evidence,
        expected_fingerprint="0" * 64,
        verify_keys={"node-v1": "node-secret"},
        env=env,
    )
    promoted = promote_patch(
        value,
        evidence,
        expected_fingerprint=evidence["patch_sha256"],
        verify_keys={"node-v1": "node-secret"},
        env=env,
    )

    assert rejected["reason"] == "patch_fingerprint_mismatch"
    assert promoted["promoted"] is True
    assert promoted["changed_files"] == ["module.py"]
    assert promoted["verification"][0]["returncode"] == 0
    assert (repo / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_failed_promotion_verification_reverses_patch(tmp_path):
    repo = initialized_repo(tmp_path)
    value, env, prepared, evidence = build_evidence(repo, tmp_path)
    cleanup_worktree(prepared)
    failing_contract = {
        **value,
        "verification_commands": [
            [sys.executable, "-c", "raise SystemExit('promotion check failed')"]
        ],
    }

    result = promote_patch(
        failing_contract,
        evidence,
        expected_fingerprint=evidence["patch_sha256"],
        verify_keys={"node-v1": "node-secret"},
        env=env,
    )

    assert result["promoted"] is False
    assert result["reason"] == "promotion_verification_failed"
    assert result["rolled_back"] is True
    assert (repo / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git(repo, "status", "--porcelain").stdout == ""
