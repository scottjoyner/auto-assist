import json
import subprocess

from assistx.improvement_cycle import build_execution_contract
from assistx.improvement_runtime import (
    collect_executor_evidence,
    prepare_repository,
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


def test_executor_attests_real_diff_and_verification(tmp_path):
    repo = initialized_repo(tmp_path)
    contract = build_execution_contract(
        repository="repo",
        objective="Change one value",
        allowed_paths=["module.py"],
        verification_commands=[
            ["python", "-c", "import module; assert module.VALUE == 2"]
        ],
    )
    prepared = prepare_repository(
        contract,
        env={"ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps({"repo": str(repo)})},
    )
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    evidence = collect_executor_evidence(
        contract,
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
    )

    assert prepared["ok"] is True
    assert evidence["evidence_source"] == "executor"
    assert evidence["worktree_clean_before"] is True
    assert evidence["scope_validated"] is True
    assert evidence["changed_files"] == ["module.py"]
    assert evidence["diff_lines"] == 2
    assert evidence["verification"][0]["returncode"] == 0


def test_dirty_worktree_is_rejected_before_agent_execution(tmp_path):
    repo = initialized_repo(tmp_path)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    contract = build_execution_contract(
        repository="repo",
        objective="Change one value",
        allowed_paths=["module.py"],
        verification_commands=[["python", "-m", "compileall", "-q", "module.py"]],
    )

    prepared = prepare_repository(
        contract,
        env={"ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps({"repo": str(repo)})},
    )

    assert prepared["ok"] is False
    assert prepared["reason"] == "worktree_not_clean"
    assert prepared["dirty_paths"] == ["module.py"]
