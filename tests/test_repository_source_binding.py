"""Source-binding contract tests.

The incident these guard: a reviewer examined a stale mirror instead of the
authoritative worktree and produced a confident, wrong verdict. Every test below
asserts that such a substitution is *rejected*, and that rejection never turns
into a silent fallback to some other checkout.
"""

from __future__ import annotations

import json
import os
import subprocess
from enum import Enum
from pathlib import Path

import pytest

from assistx.contracts.schemas.repository_source_binding import (
    SOURCE_BINDING_SCHEMA,
    DirtyStateExpectation,
    RepositorySourceBinding,
)
from assistx.improvement_cycle import (
    build_execution_contract,
    task_source_binding,
)
from assistx.repository_source_binding import (
    SourceBindingState,
    bind_repository_source,
    observe_source,
    verify_source_binding,
)


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **(env or {})},
    )
    return result.stdout.strip()


#: Pinned so two identically-seeded repos always produce the same commit SHA.
#: Wall-clock timestamps made the "identical HEAD, different directory" cases
#: depend on both commits landing in the same second, which is why they
#: intermittently failed for no reason connected to the behaviour under test.
FIXED_DATE = "2026-01-01T00:00:00+00:00"


def make_repo(root: Path, name: str = "project") -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init")
    git(repo, "config", "user.email", "canary@example.com")
    git(repo, "config", "user.name", "Canary")
    (repo / "README.md").write_text("seed\n")
    git(repo, "add", "README.md")
    git(
        repo,
        "commit",
        "-m",
        "seed",
        env={"GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE},
    )
    git(repo, "branch", "-M", "main")
    return repo


def make_worktree(repo: Path, path: Path, branch: str | None = None) -> Path:
    name = branch or f"task-{path.name}"
    git(repo, "worktree", "add", "-b", name, str(path), "HEAD")
    return path


def registry_env(repo: Path) -> dict[str, str]:
    return {"ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps({"project": str(repo)})}


def binding_for(repo: Path, worktree: Path, work_id: str = "work-1"):
    """Pin the binding to `worktree`, anchored on the registered repo root."""
    binding, reason = bind_repository_source(
        "project",
        worktree,
        work_id=work_id,
        env=registry_env(repo),
    )
    assert binding is not None, reason
    return binding


# --- exact requested worktree + HEAD -> accepted ---------------------------


def test_exact_worktree_and_head_is_accepted(tmp_path):
    repo = make_repo(tmp_path)
    worktree = make_worktree(repo, tmp_path / "wt")

    binding = binding_for(repo, worktree)
    verification = verify_source_binding(binding, observe_source(worktree))

    assert verification.state is SourceBindingState.MATCH
    assert verification.matched is True
    assert verification.fallback_attempted is False
    assert verification.grants_authority is False


def test_binding_records_repository_and_worktree_identity(tmp_path):
    repo = make_repo(tmp_path)
    worktree = make_worktree(repo, tmp_path / "wt")

    binding = binding_for(repo, worktree)

    assert binding.repository == "project"
    assert binding.repository_realpath == str(repo.resolve())
    assert binding.worktree_realpath == str(worktree.resolve())
    assert binding.repository_realpath != binding.worktree_realpath
    assert binding.branch == "task-wt"
    assert len(binding.head_sha) == 40
    assert binding.expect_dirty is DirtyStateExpectation.CLEAN


# --- same repo / different worktree -> rejected ---------------------------


def test_same_repository_different_worktree_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    pinned = make_worktree(repo, tmp_path / "pinned")
    other = make_worktree(repo, tmp_path / "other")

    binding = binding_for(repo, pinned)
    verification = verify_source_binding(binding, observe_source(other))

    assert verification.state is SourceBindingState.WORKTREE_MISMATCH
    assert verification.matched is False
    # Same repository, same HEAD, still rejected: a sibling worktree of the
    # pinned repository is not a substitute for the pinned worktree.
    assert verification.observed.repository_realpath == binding.repository_realpath
    assert verification.observed.head_sha == binding.head_sha
    assert verification.fallback_attempted is False


# --- same worktree / stale HEAD -> rejected -------------------------------


def test_same_worktree_with_stale_head_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    worktree = make_worktree(repo, tmp_path / "wt")

    binding = binding_for(repo, worktree)

    # The pinned worktree advances after the binding was minted. The directory
    # is identical, so only a HEAD comparison catches it.
    (worktree / "README.md").write_text("moved on\n")
    git(worktree, "add", "README.md")
    git(
        worktree,
        "commit",
        "-m",
        "later work",
        env={"GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE},
    )

    verification = verify_source_binding(binding, observe_source(worktree))

    assert verification.state is SourceBindingState.HEAD_MISMATCH
    assert verification.matched is False
    assert verification.fallback_attempted is False


# --- different mirror with same repo name -> rejected ---------------------


def test_different_mirror_with_same_repository_name_is_rejected(tmp_path):
    """A stale copy of the project is not the registered repository."""
    authoritative = make_repo(tmp_path / "a")
    mirror = make_repo(tmp_path / "b")

    binding = binding_for(authoritative, authoritative)

    assert binding.repository == "project"
    verification = verify_source_binding(binding, observe_source(mirror))

    assert verification.state is SourceBindingState.REPOSITORY_MISMATCH
    assert verification.matched is False
    assert verification.observed.repository_realpath != binding.repository_realpath
    assert verification.fallback_attempted is False


def test_mirror_is_never_mistaken_for_the_pinned_repository(tmp_path):
    """Even at an identical HEAD, the wrong checkout is rejected."""
    authoritative = make_repo(tmp_path / "a")
    mirror = make_repo(tmp_path / "b")
    head = git(authoritative, "rev-parse", "HEAD")

    assert git(mirror, "rev-parse", "HEAD") == head

    binding = binding_for(authoritative, authoritative)
    verification = verify_source_binding(binding, observe_source(mirror))

    assert verification.state is SourceBindingState.REPOSITORY_MISMATCH


def test_unregistered_repository_is_refused_by_the_registry(tmp_path):
    repo = make_repo(tmp_path)

    binding, reason = bind_repository_source(
        "project",
        repo,
        work_id="work-1",
        env={"ASSISTX_REPOSITORY_ROOTS_JSON": "{}"},
    )

    assert binding is None
    assert reason == "repository_root_not_configured"


def test_caller_supplied_path_outside_registry_is_refused(tmp_path):
    """A caller cannot promote an arbitrary host path into an identity."""
    registered = make_repo(tmp_path / "registered")
    elsewhere = make_repo(tmp_path / "elsewhere")

    binding, reason = bind_repository_source(
        "project",
        elsewhere,
        work_id="work-1",
        env=registry_env(registered),
    )

    assert binding is None
    assert reason == "registered_repository_root_mismatch"


# --- missing worktree -> rejected -----------------------------------------


def test_missing_worktree_is_rejected_as_source_unavailable(tmp_path):
    repo = make_repo(tmp_path)
    worktree = make_worktree(repo, tmp_path / "wt")
    binding = binding_for(repo, worktree)

    import shutil

    shutil.rmtree(worktree)

    verification = verify_source_binding(binding, observe_source(worktree))

    assert verification.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verification.matched is False
    assert verification.fallback_attempted is False


def test_non_git_directory_is_rejected_as_source_unavailable(tmp_path):
    repo = make_repo(tmp_path)
    binding = binding_for(repo, repo)
    plain = tmp_path / "plain"
    plain.mkdir()

    verification = verify_source_binding(binding, observe_source(plain))

    assert verification.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verification.fallback_attempted is False


def test_home_directory_is_not_accepted_as_a_substitute(tmp_path, monkeypatch):
    """$HOME is never a fallback for an unavailable pinned worktree."""
    repo = make_repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    # A real git checkout of a same-named project now exists under $HOME.
    make_repo(home)
    monkeypatch.setenv("HOME", str(home))

    worktree = make_worktree(repo, tmp_path / "wt")
    binding = binding_for(repo, worktree)

    import shutil

    shutil.rmtree(worktree)

    # A same-named checkout now exists under $HOME. It must not be adopted.
    from_home = observe_source(home / "project")
    verification = verify_source_binding(binding, from_home)

    assert verification.state is SourceBindingState.REPOSITORY_MISMATCH
    assert verification.matched is False
    assert verification.fallback_attempted is False


# --- non-repository task -> unaffected ------------------------------------


def test_non_repository_task_contract_is_unaffected(tmp_path):
    contract = build_execution_contract(
        repository="some-repo",
        objective="do a bounded thing",
        allowed_paths=["src/app.py"],
        verification_commands=[["pytest", "-q"]],
    )

    assert "source_binding" not in contract
    assert contract["version"] == 2
    assert contract["kind"] == "bounded_code_change"
    assert task_source_binding({"payload": {"execution_contract": contract}}) is None


def test_source_binding_is_recorded_as_provenance_when_supplied(tmp_path):
    repo = make_repo(tmp_path)
    worktree = make_worktree(repo, tmp_path / "wt")
    binding = binding_for(repo, worktree)

    contract = build_execution_contract(
        repository="project",
        objective="bound edit",
        allowed_paths=["README.md"],
        verification_commands=[["pytest", "-q"]],
        source_binding=binding,
    )

    assert contract["source_binding"]["schema_version"] == SOURCE_BINDING_SCHEMA
    recovered = task_source_binding({"payload": {"execution_contract": contract}})
    assert recovered is not None
    assert recovered.head_sha == binding.head_sha
    assert recovered.worktree_realpath == binding.worktree_realpath
    # Recording provenance grants nothing.
    assert verify_source_binding(binding, observe_source(worktree)).grants_authority is False


# --- contract-level fail-closed validation --------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "abc123"),
        ("head_sha", "z" * 40),
        ("repository_realpath", "relative/path"),
        ("worktree_realpath", "/repo/../elsewhere"),
        ("worktree_realpath", "/repo//sub"),
        ("worktree_realpath", "/repo/"),
        ("repository", "a/b"),
        ("branch", ""),
        ("work_id", ""),
        ("source_manifest_sha256", "not-a-digest"),
    ],
)
def test_contract_rejects_malformed_fields(field, value):
    base = {
        "repository": "project",
        "repository_realpath": "/srv/repos/project",
        "worktree_realpath": "/srv/worktrees/project-task",
        "branch": "main",
        "head_sha": "a" * 40,
        "work_id": "work-1",
    }
    base[field] = value

    with pytest.raises(ValueError):
        RepositorySourceBinding(**base)


def test_contract_rejects_unknown_fields():
    """extra='forbid' keeps a stray fallback hint from riding along."""
    with pytest.raises(ValueError):
        RepositorySourceBinding(
            repository="project",
            repository_realpath="/srv/repos/project",
            worktree_realpath="/srv/worktrees/project-task",
            branch="main",
            head_sha="a" * 40,
            work_id="work-1",
            fallback_worktree="/home/scott/embed_x1",
        )


def test_dirty_state_expectation_is_reported_separately(tmp_path):
    repo = make_repo(tmp_path)
    worktree = make_worktree(repo, tmp_path / "wt")
    binding = binding_for(repo, worktree)

    (worktree / "README.md").write_text("uncommitted\n")

    verification = verify_source_binding(binding, observe_source(worktree))

    assert verification.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert verification.observed.repository_realpath == binding.repository_realpath
    assert verification.observed.worktree_realpath == binding.worktree_realpath
    assert verification.observed.head_sha == binding.head_sha


def test_any_dirty_expectation_admits_a_dirty_worktree(tmp_path):
    repo = make_repo(tmp_path)
    worktree = make_worktree(repo, tmp_path / "wt")

    binding, reason = bind_repository_source(
        "project",
        worktree,
        work_id="work-1",
        expect_dirty=DirtyStateExpectation.ANY,
        env=registry_env(repo),
    )
    assert binding is not None, reason

    (worktree / "README.md").write_text("uncommitted\n")

    verification = verify_source_binding(binding, observe_source(worktree))
    assert verification.state is SourceBindingState.MATCH


# --- the contract stays inside the merge-gate lint gate --------------------


def _merge_gate_lint_step() -> str:
    """Return the merge-gate ruff step body, parsed as plain text (no yaml dep)."""
    repo_root = Path(__file__).resolve().parents[1]
    workflow = (repo_root / ".github/workflows/ci.yml").read_text()
    _, _, after = workflow.partition("Lint merge-gate control modules (ruff)")
    assert after, "merge-gate lint step disappeared from ci.yml"
    body, _, _ = after.partition("Formatting audit")
    return body


def test_source_binding_modules_are_lint_gated():
    """The contract is merge-gate code, so it must stay in the gate's file list.

    Dropping these paths from ci.yml would leave a source-integrity contract
    that silently rots, which is how the wrong-checkout failure stayed invisible
    in the first place.
    """
    step = _merge_gate_lint_step()

    for gated in (
        "src/assistx/repository_source_binding.py",
        "src/assistx/contracts/schemas/repository_source_binding.py",
        "tests/test_repository_source_binding.py",
    ):
        assert gated in step, f"{gated} is not covered by the merge-gate lint"


def test_str_enum_modernization_rule_stays_waived():
    """`class X(str, Enum)` must not fail the gate.

    UP042 would demand StrEnum, which is Python 3.11+, while auto-ingest declares
    requires-python >=3.10 and imports this contract. The rule is waived in the
    same spirit as the other UP modernization rules already in the ignore list.
    """
    step = _merge_gate_lint_step()
    ignore_line = next(
        line for line in step.splitlines() if "ruff check --ignore" in line
    )

    assert "UP042" in ignore_line


def test_contract_enums_use_the_shared_str_enum_style():
    """Pin the `str, Enum` contract style so UP042 stays a conscious waiver."""
    for enum_class in (DirtyStateExpectation, SourceBindingState):
        assert issubclass(enum_class, str)
        assert issubclass(enum_class, Enum)
