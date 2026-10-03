"""Contract tests for repository source binding.

These encode the incident that motivated the contract: a swarm reviewer read
``/home/scott/embed_x1`` instead of the authoritative worktree, produced a
well-formed review of stale source, and the error was only discoverable after
the fact. Each test below pins one way that must fail closed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from assistx.contracts.schemas.repository_source_binding import (
    BINDING_AUTHORITY_SOURCE,
    DirtyStateExpectation,
    ObservedSourceState,
    RepositorySourceBinding,
    SourceBindingState,
)
from assistx.repository_source_verifier import (
    FORBIDDEN_MIRROR,
    binding_from_payload,
    build_binding,
    configured_repository_roots,
    observe_source,
    verify_source_binding,
    verify_workspace,
)


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def make_repo(root: Path, name: str = "auto-ingest-swarm") -> Path:
    """Create a real git repository with one commit and return its path."""

    root.mkdir(parents=True, exist_ok=True)
    git("init", "-b", "main", cwd=root)
    git("config", "user.email", "test@example.invalid", cwd=root)
    git("config", "user.name", "Source Binding Test", cwd=root)
    (root / "service.py").write_text(
        "def healthy(status: str) -> bool:\n    return status == 'online'\n",
        encoding="utf-8",
    )
    git("add", ".", cwd=root)
    git("commit", "-m", "initial", cwd=root)
    return root


def binding_for(repo: Path, **overrides) -> RepositorySourceBinding:
    observed = observe_source(repo)
    assert observed.available, observed.unavailable_reason
    payload = {
        "repository": "auto-ingest-swarm",
        "repo_realpath": observed.repo_realpath,
        "worktree_realpath": observed.worktree_realpath,
        "branch": observed.branch,
        "head_sha": observed.head_sha,
        "task_id": "task-1",
        "work_id": "work-1",
    }
    payload.update(overrides)
    return RepositorySourceBinding(**payload)


# --- required case 1: exact requested worktree + HEAD -> accepted ---------


def test_exact_requested_worktree_and_head_is_accepted(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)

    verdict = verify_workspace(binding, repo)

    assert verdict.state is SourceBindingState.MATCH
    assert verdict.accepted is True
    assert verdict.reasons == []
    assert verdict.fallback_candidates_considered == []


# --- required case 2: same repo / different worktree -> rejected ----------


def test_same_repository_different_worktree_is_rejected(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    sibling = make_repo(tmp_path / "auto-ingest-swarm-review")
    binding = binding_for(repo)

    verdict = verify_workspace(binding, sibling)

    # The two directories are separate repositories, so identity fails first.
    assert verdict.state is SourceBindingState.REPOSITORY_MISMATCH
    assert verdict.accepted is False


def test_real_sibling_worktree_of_the_same_repository_is_rejected(tmp_path):
    """A genuine ``git worktree`` of the bound repo is still not the bound tree."""

    repo = make_repo(tmp_path / "auto-ingest-swarm")
    head = git("rev-parse", "HEAD", cwd=repo)
    sibling = tmp_path / "auto-ingest-swarm-review"
    git("worktree", "add", "--detach", str(sibling), head, cwd=repo)

    binding = binding_for(repo)
    verdict = verify_workspace(binding, sibling)

    assert verdict.state is SourceBindingState.WORKTREE_MISMATCH
    assert verdict.accepted is False
    assert verdict.observed_repo_realpath == binding.repo_realpath
    assert "not an accepted substitute" in verdict.reasons[0]


# --- required case 3: same worktree / stale HEAD -> rejected -------------


def test_same_worktree_with_stale_head_is_rejected(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)
    (repo / "service.py").write_text("def healthy(s): return True\n", encoding="utf-8")
    git("add", ".", cwd=repo)
    git("commit", "-m", "second", cwd=repo)

    verdict = verify_workspace(binding, repo)

    assert verdict.state is SourceBindingState.HEAD_MISMATCH
    assert verdict.accepted is False
    assert "stale" in verdict.reasons[0]


def test_detached_head_that_matches_the_bound_sha_is_accepted(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    head = git("rev-parse", "HEAD", cwd=repo)
    detached = tmp_path / "auto-ingest-swarm-detached"
    git("worktree", "add", "--detach", str(detached), head, cwd=repo)

    binding = binding_for(detached)
    verdict = verify_workspace(binding, detached)

    assert verdict.state is SourceBindingState.MATCH


# --- required case 4: different mirror with same repo name -> rejected ----


def test_different_mirror_with_the_same_repository_name_is_rejected(tmp_path):
    authoritative = make_repo(tmp_path / "worktrees" / "auto-ingest-swarm-20261002")
    stale_mirror = make_repo(tmp_path / "embed_x1")

    binding = binding_for(authoritative)
    verdict = verify_workspace(binding, stale_mirror)

    assert verdict.accepted is False
    assert verdict.state is SourceBindingState.REPOSITORY_MISMATCH
    assert verdict.observed_worktree_realpath == str(stale_mirror.resolve())


def test_stale_mirror_is_never_silently_substituted(tmp_path):
    """The verifier must not walk away from a missing bound worktree."""

    authoritative = make_repo(tmp_path / "worktrees" / "auto-ingest-swarm-20261002")
    make_repo(tmp_path / "embed_x1")
    binding = binding_for(authoritative)

    # The bound worktree is gone. A mirror with a similar name exists.
    import shutil

    shutil.rmtree(authoritative)
    verdict = verify_workspace(binding, authoritative)

    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verdict.fallback_candidates_considered == []
    assert verdict.observed_worktree_realpath is None


# --- required case 5: missing worktree -> rejected -----------------------


def test_missing_worktree_is_rejected(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)
    gone = tmp_path / "not-a-checkout"

    verdict = verify_workspace(binding, gone)

    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verdict.accepted is False
    assert verdict.reasons == ["workspace_path_is_not_a_directory"]


def test_none_workspace_is_source_unavailable(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    verdict = verify_workspace(binding_for(repo), None)

    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verdict.reasons == ["no_workspace_path_supplied"]


def test_non_git_directory_is_source_unavailable(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    plain = tmp_path / "plain"
    plain.mkdir()

    verdict = verify_workspace(binding_for(repo), plain)

    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verdict.reasons == ["workspace_is_not_a_git_worktree"]


# --- required case 6: non-repository task -> unaffected ------------------


def test_non_repository_task_is_unaffected():
    payload = {
        "kind": "voice_task",
        "execution_mode": "analysis_only",
        "repository": None,
    }

    assert binding_from_payload(payload) is None
    assert binding_from_payload(None) is None
    assert binding_from_payload({}) is None
    assert binding_from_payload({"source_binding": None}) is None


def test_binding_is_not_authority(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    verdict = verify_workspace(binding_for(repo), repo)

    assert verdict.dispatch_allowed is False
    assert verdict.approval_granted is False
    assert verdict.claim_acquired is False
    assert verdict.mutation_allowed is False
    assert verdict.execution_authority_granted is False
    assert verdict.routing_authority_changed is False


# --- fail-closed validation of the contract itself -----------------------


def test_binding_rejects_home_directory_as_a_source(tmp_path):
    with pytest.raises(ValueError, match="home directory"):
        RepositorySourceBinding(
            repository="auto-ingest-swarm",
            repo_realpath=str(tmp_path),
            worktree_realpath=str(Path.home()),
            branch="main",
            head_sha="a" * 40,
            task_id="t",
            work_id="w",
        )


@pytest.mark.parametrize("bad", ["relative/path", "~/repo", "/a/../b", "/a//b"])
def test_binding_rejects_non_canonical_paths(bad):
    with pytest.raises(ValueError):
        RepositorySourceBinding(
            repository="auto-ingest-swarm",
            repo_realpath=bad,
            worktree_realpath=bad,
            branch="main",
            head_sha="a" * 40,
            task_id="t",
            work_id="w",
        )


def test_binding_rejects_a_path_smuggled_in_as_repository_identity():
    with pytest.raises(ValueError, match="not a host path"):
        RepositorySourceBinding(
            repository="/home/scott/embed_x1",
            repo_realpath="/repos/auto-ingest",
            worktree_realpath="/worktrees/auto-ingest",
            branch="main",
            head_sha="a" * 40,
            task_id="t",
            work_id="w",
        )


def test_binding_rejects_arbitrary_authority_source():
    with pytest.raises(ValueError, match="never authoritative on its own"):
        RepositorySourceBinding(
            authority_source="caller_supplied",
            repository="auto-ingest-swarm",
            repo_realpath="/repos/auto-ingest",
            worktree_realpath="/worktrees/auto-ingest",
            branch="main",
            head_sha="a" * 40,
            task_id="t",
            work_id="w",
        )


def test_binding_defaults_to_the_pinned_authority_source():
    binding = RepositorySourceBinding(
        repository="auto-ingest-swarm",
        repo_realpath="/repos/auto-ingest",
        worktree_realpath="/worktrees/auto-ingest",
        branch="main",
        head_sha="a" * 40,
        task_id="t",
        work_id="w",
    )
    assert binding.authority_source == BINDING_AUTHORITY_SOURCE


def test_binding_rejects_malformed_head_and_manifest():
    with pytest.raises(ValueError):
        RepositorySourceBinding(
            repository="auto-ingest-swarm",
            repo_realpath="/repos/auto-ingest",
            worktree_realpath="/worktrees/auto-ingest",
            branch="main",
            head_sha="not-a-sha",
            task_id="t",
            work_id="w",
        )
    with pytest.raises(ValueError):
        RepositorySourceBinding(
            repository="auto-ingest-swarm",
            repo_realpath="/repos/auto-ingest",
            worktree_realpath="/worktrees/auto-ingest",
            branch="main",
            head_sha="a" * 40,
            source_manifest_sha256="short",
            task_id="t",
            work_id="w",
        )


def test_malformed_binding_raises_rather_than_degrading_to_unbound(tmp_path):
    payload = {"source_binding": {"repository": "auto-ingest-swarm"}}

    with pytest.raises(ValueError):
        binding_from_payload(payload)


def test_binding_round_trips_through_a_payload(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)

    restored = binding_from_payload({"source_binding": binding.provenance()})

    assert restored is not None
    assert restored == binding


# --- configuration is the only authority for repository identity --------


def test_build_binding_requires_a_configured_alias(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")

    with pytest.raises(ValueError, match="not configured"):
        build_binding(
            repository="auto-ingest-swarm",
            worktree_path=repo,
            task_id="t",
            work_id="w",
            env={},
        )


def test_build_binding_rejects_a_worktree_from_another_repository(tmp_path):
    configured = make_repo(tmp_path / "repos" / "auto-ingest-swarm")
    impostor = make_repo(tmp_path / "embed_x1")
    env = {"ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps({"auto-ingest-swarm": str(configured)})}

    with pytest.raises(ValueError, match="does not belong to the configured repository"):
        build_binding(
            repository="auto-ingest-swarm",
            worktree_path=impostor,
            task_id="t",
            work_id="w",
            env=env,
        )


def test_build_binding_succeeds_for_the_configured_worktree(tmp_path):
    repo = make_repo(tmp_path / "repos" / "auto-ingest-swarm")
    env = {"ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps({"auto-ingest-swarm": str(repo)})}

    binding = build_binding(
        repository="auto-ingest-swarm",
        worktree_path=repo,
        task_id="t",
        work_id="w",
        env=env,
    )

    assert binding.repository == "auto-ingest-swarm"
    assert binding.repo_realpath == str(repo.resolve())
    assert verify_workspace(binding, repo).accepted is True


def test_configured_repository_roots_ignores_tilde_and_malformed_entries():
    roots = configured_repository_roots(
        {
            "ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps(
                {
                    "good": "/repos/good",
                    "tilde": "~/repos/tilde",
                    "blank": "   ",
                    "empty": "",
                }
            )
        }
    )

    assert set(roots) == {"good"}


def test_configured_repository_roots_fails_closed_on_bad_json():
    assert configured_repository_roots({"ASSISTX_REPOSITORY_ROOTS_JSON": "{oops"}) == {}
    assert configured_repository_roots({"ASSISTX_REPOSITORY_ROOTS_JSON": "[]"}) == {}


# --- dirty-state expectation and provenance -----------------------------


def test_dirty_worktree_is_rejected_when_a_clean_tree_was_bound(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)
    (repo / "service.py").write_text("dirty\n", encoding="utf-8")

    verdict = verify_workspace(binding, repo)

    assert verdict.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert verdict.observed_dirty is True


def test_dirty_expectation_any_accepts_a_dirty_tree(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo, expected_dirty=DirtyStateExpectation.ANY)
    (repo / "service.py").write_text("dirty\n", encoding="utf-8")

    assert verify_workspace(binding, repo).accepted is True


def test_verdict_provenance_proves_what_was_examined(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)
    (repo / "service.py").write_text("dirty\n", encoding="utf-8")

    provenance = verify_workspace(binding, repo).provenance()

    assert provenance["state"] == "DIRTY_STATE_MISMATCH"
    assert provenance["observed_worktree_realpath"] == str(repo.resolve())
    assert provenance["expected_head_sha"] == binding.head_sha
    assert provenance["fallback_candidates_considered"] == []
    assert json.dumps(provenance)


# --- the forbidden mirror is named but never used ------------------------


def test_verifier_never_offers_the_incident_mirror_as_a_fallback(tmp_path):
    authoritative = make_repo(tmp_path / "worktrees" / "auto-ingest-swarm-20261002")
    binding = binding_for(authoritative)

    verdict = verify_workspace(binding, tmp_path / "definitely-absent")

    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verdict.fallback_candidates_considered == []
    assert FORBIDDEN_MIRROR not in json.dumps(verdict.provenance())


def test_verify_source_binding_accepts_a_hand_built_observation(tmp_path):
    """The verifier takes any observed state, so an executor can supply one."""

    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)
    observed = ObservedSourceState(
        repo_realpath=binding.repo_realpath,
        worktree_realpath=binding.worktree_realpath,
        branch=binding.branch,
        head_sha=binding.head_sha,
        dirty=False,
    )

    assert verify_source_binding(binding, observed).state is SourceBindingState.MATCH


def test_unavailable_observation_short_circuits_before_identity_checks(tmp_path):
    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)

    verdict = verify_source_binding(binding, ObservedSourceState.unavailable("gone"))

    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verdict.reasons == ["gone"]

# --- call site: repository tasks record the tree they were generated from --


def test_repo_analysis_task_payload_carries_a_source_binding(tmp_path):
    import assistx.repo_task_generator as generator

    repo = make_repo(tmp_path / "auto-ingest-swarm")
    source = repo / "service.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    info = generator._get_repo_info(repo, "auto-ingest-swarm")
    assert info is not None

    tasks = generator._create_tasks_for_repo(repo, max_per_repo=1)
    analysis = next(t for t in tasks if t["kind"].startswith("repo_"))

    binding = binding_from_payload(analysis["payload"])
    assert binding is not None
    assert binding.repository == "auto-ingest-swarm"
    assert binding.worktree_realpath == str(repo.resolve())
    assert binding.head_sha == info["commit"]
    assert binding.task_id == analysis["id"]
    # A reviewer can now prove which tree the task was generated against.
    assert verify_workspace(binding, repo).accepted is True
    # Carrying a binding changed no authority surface: the task still declares
    # its own execution mode and approval gate exactly as before.
    assert analysis["payload"]["execution_mode"] == "analysis_only"
    assert analysis["payload"]["requires_approval"] is False
    assert analysis["requires_approval"] is (not generator.REPO_TASK_AUTO_READY)
    assert "dispatch_allowed" not in analysis["payload"]
    assert "claim_acquired" not in analysis["payload"]
    assert "routing_authority_changed" not in analysis["payload"]


def test_source_binding_payload_survives_a_missing_repo_root(tmp_path):
    import assistx.repo_task_generator as generator

    repo = make_repo(tmp_path / "auto-ingest-swarm")
    info = dict(generator._get_repo_info(repo, "auto-ingest-swarm"))
    info.pop("repo_root")

    payload = generator._source_binding_payload(info, "task-1")

    assert payload is not None
    assert payload["repo_realpath"] == payload["worktree_realpath"]


def test_source_binding_payload_returns_none_when_identity_is_unusable():
    import assistx.repo_task_generator as generator

    assert generator._source_binding_payload({"alias": "", "path": ""}, "task-1") is None


# --- a failed git command must never be mistaken for a good value ---------
#
# ``repo_task_generator._get_repo_info`` used to collapse a non-zero git return
# into "empty", so a broken repository looked clean. observe_source must
# distinguish the two; these tests pin that every git failure fails closed
# rather than degrading into a plausible-looking value.


def _fail_only(monkeypatch, verifier, failing: list[str]):
    real_git = verifier._git

    def patched(args, cwd):
        if args[: len(failing)] == failing:
            return 1, ""
        return real_git(args, cwd)

    monkeypatch.setattr(verifier, "_git", patched)


@pytest.mark.parametrize(
    ("failing", "reason"),
    [
        (["status", "--porcelain"], "git_status_unreadable"),
        (["rev-parse", "HEAD"], "git_head_unreadable"),
        (["rev-parse", "--git-common-dir"], "repository_common_dir_unresolvable"),
    ],
)
def test_failed_git_command_is_source_unavailable_not_a_default_value(
    tmp_path, monkeypatch, failing, reason
):
    import assistx.repository_source_verifier as verifier

    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)
    _fail_only(monkeypatch, verifier, failing)

    verdict = verifier.verify_workspace(binding, repo)

    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verdict.reasons == [reason]
    # Never a silently-passing observation.
    assert verdict.accepted is False


def test_failed_head_read_does_not_reuse_the_bound_sha(tmp_path, monkeypatch):
    """Regression: an unreadable HEAD must not fall back to the expected SHA."""

    import assistx.repository_source_verifier as verifier

    repo = make_repo(tmp_path / "auto-ingest-swarm")
    binding = binding_for(repo)
    _fail_only(monkeypatch, verifier, ["rev-parse", "HEAD"])

    verdict = verifier.verify_workspace(binding, repo)

    assert verdict.observed_head_sha is None
    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE


def test_hostile_workspace_path_fails_closed_rather_than_raising(tmp_path):
    """A NUL byte must be a SOURCE_UNAVAILABLE, not an uncaught ValueError."""

    import assistx.repository_source_verifier as verifier

    binding = RepositorySourceBinding(
        repository="auto-ingest-swarm",
        repo_realpath=str(tmp_path),
        worktree_realpath=str(tmp_path / "wt"),
        branch="main",
        head_sha="a" * 40,
        task_id="t",
        work_id="w",
    )

    verdict = verifier.verify_workspace(binding, "\x00invalid")

    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verdict.reasons == ["workspace_path_contains_control_characters"]
    assert verdict.accepted is False


# --- import isolation ----------------------------------------------------
#
# The contract is meant to be consumable cross-repository without dragging in
# AssistX runtime state. That claim is only true when the file is loaded
# directly: importing through the `assistx` package executes
# `assistx/__init__.py`, which installs five runtime safety boundaries at import
# time. Both properties are pinned here so the docstring cannot drift into
# another falsehood.

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "assistx"
    / "contracts"
    / "schemas"
    / "repository_source_binding.py"
)


def _load_contract_standalone():
    """Load the contract by path, the way a cross-repo consumer must."""

    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "repository_source_binding_standalone", _CONTRACT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    # Required: with `from __future__ import annotations`, pydantic resolves the
    # string annotations through sys.modules[cls.__module__] and otherwise
    # reports the model as not fully defined.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_contract_loads_standalone_without_assistx_or_runtime_state(tmp_path):
    """Isolation must be measured in a clean interpreter.

    This test session has already imported `assistx` through the verifier tests,
    so inspecting sys.modules here would measure the session, not the load. A
    subprocess is the only honest way to show what importing the contract costs
    on its own.
    """

    script = tmp_path / "isolate.py"
    script.write_text(
        "import importlib.util, sys, pathlib\n"
        f"spec = importlib.util.spec_from_file_location('rsb', {str(_CONTRACT_PATH)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "roots = {n.split('.')[0] for n in sys.modules}\n"
        "forbidden = {'assistx', 'neo4j', 'fastapi', 'httpx', 'pandas', 'numpy', 'starlette'}\n"
        "print(','.join(sorted(roots & forbidden)))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "", (
        "standalone contract load pulled runtime modules: " + result.stdout.strip()
    )


def test_standalone_contract_still_validates_and_grants_nothing():
    module = _load_contract_standalone()

    binding = module.RepositorySourceBinding(
        repository="auto-ingest-swarm",
        repo_realpath="/repos/auto-ingest",
        worktree_realpath="/worktrees/auto-ingest-swarm-20261002",
        branch="main",
        head_sha="a" * 40,
        task_id="t",
        work_id="w",
    )
    assert binding.expected_dirty is module.DirtyStateExpectation.CLEAN
    assert module.SourceBindingState.MATCH.value == "MATCH"

    verdict = module.SourceBindingVerdict(
        state=module.SourceBindingState.MATCH,
        task_id="t",
        work_id="w",
        repository="auto-ingest-swarm",
        expected_repo_realpath=binding.repo_realpath,
        expected_worktree_realpath=binding.worktree_realpath,
        expected_branch="main",
        expected_head_sha=binding.head_sha,
    )
    assert verdict.dispatch_allowed is False
    assert verdict.claim_acquired is False
    assert verdict.execution_authority_granted is False
    assert verdict.routing_authority_changed is False


def test_package_import_is_documented_as_unsafe_for_isolation():
    """Pin the reason the standalone recipe exists.

    If this ever stops holding - if `assistx/__init__.py` becomes import-inert -
    the docstring's warning is stale and should be rewritten, not silently kept.
    """

    import assistx
    import assistx.strict_claims  # noqa: F401
    import assistx.task_family_routing  # noqa: F401

    assert hasattr(assistx, "_install_runtime_safety_boundaries")
    assert assistx.__doc__ is None or "runtime_safety" not in (assistx.__doc__ or "")
    # The installers run at import time, not on demand.
    import sys

    assert "assistx.strict_claims" in sys.modules
