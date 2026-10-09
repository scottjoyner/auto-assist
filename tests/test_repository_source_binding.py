"""Repository source-binding contract and verifier tests.

The regression these lock down: a delegated review inspected a stale mirror
instead of the requested worktree and produced a well-formed, wrong review.
Nothing about that result distinguished it from a correct one. A binding makes
the failure explicit and terminal.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from assistx.contracts.repository_source_verifier import (
    ObservedSource,
    build_source_binding,
    observe_source_workspace,
    verify_repository_source,
    verify_source_workspace,
)
from assistx.contracts.schemas.repository_source_binding import (
    DirtyStateExpectation,
    RepositorySourceBinding,
    SourceBindingState,
)
from assistx.improvement_cycle import (
    build_execution_contract,
    build_work_packet,
    evaluate_completion,
)
from assistx.improvement_runtime import sign_executor_evidence

HEAD_A = "a" * 40
HEAD_B = "b" * 40
HEAD_C = "c" * 40
MAIN_REPO = "/srv/repos/auto-assist"
BOUND_WORKTREE = "/srv/worktrees/auto-ingest-swarm-20261002"
STALE_MIRROR = "/home/scott/embed_x1"


def _binding(**overrides) -> RepositorySourceBinding:
    values = {
        "repository": "auto-assist",
        "repo_realpath": MAIN_REPO,
        "worktree_realpath": BOUND_WORKTREE,
        "branch": "main",
        "head_sha": HEAD_A,
        "expected_dirty": DirtyStateExpectation.CLEAN,
        "task_id": "task-1",
        "work_id": "work-1",
    }
    values.update(overrides)
    return RepositorySourceBinding(**values)


def _observed(**overrides) -> ObservedSource:
    values = {
        "available": True,
        "worktree_realpath": BOUND_WORKTREE,
        "repository_realpath": MAIN_REPO,
        "head_sha": HEAD_A,
        "branch": "main",
        "dirty": False,
    }
    values.update(overrides)
    return ObservedSource(**values)


# --------------------------------------------------------------------------
# Contract validation: fail closed; a supplied host path is never authoritative
# --------------------------------------------------------------------------


def test_binding_rejects_path_shaped_repository_identity():
    with pytest.raises(ValueError):
        _binding(repository=STALE_MIRROR)
    with pytest.raises(ValueError):
        _binding(repository="../../etc")


def test_binding_rejects_non_canonical_or_traversable_paths():
    with pytest.raises(ValueError):
        _binding(worktree_realpath="auto-ingest-swarm-20261002")
    with pytest.raises(ValueError):
        _binding(worktree_realpath="/srv/worktrees/../worktrees/auto-ingest")
    with pytest.raises(ValueError):
        _binding(worktree_realpath="/srv/worktrees/auto-ingest/")


def test_binding_rejects_home_directory_and_filesystem_root():
    with pytest.raises(ValueError):
        _binding(worktree_realpath=os.path.expanduser("~"))
    with pytest.raises(ValueError):
        _binding(repo_realpath="/")


def test_binding_rejects_short_or_non_hex_head():
    with pytest.raises(ValueError):
        _binding(head_sha="abc123")
    with pytest.raises(ValueError):
        _binding(head_sha="z" * 40)


def test_binding_rejects_unknown_fields_including_suggested_fallbacks():
    with pytest.raises(ValueError):
        _binding(fallback_path=STALE_MIRROR)


def test_unbound_payload_is_none_but_present_invalid_binding_raises():
    assert RepositorySourceBinding.from_contract_payload(None) is None
    with pytest.raises(ValueError):
        RepositorySourceBinding.from_contract_payload({})
    with pytest.raises(ValueError):
        RepositorySourceBinding.from_contract_payload({"repository": "auto-assist"})


def test_explicit_invalid_binding_must_not_downgrade_to_unbound():
    for supplied in ({}, [], "untrusted", {"repo_realpath": "/tmp/other"}):
        with pytest.raises(ValueError):
            RepositorySourceBinding.from_contract_payload(supplied)
    valid = _binding()
    assert RepositorySourceBinding.from_contract_payload(
        valid.to_contract_payload()
    ) == valid
    for field in ("work_id", "task_id", "authority_source", "repo_realpath"):
        mangled = valid.to_contract_payload()
        mangled.pop(field)
        if field == "authority_source":
            # The schema supplies the same pinned default for omitted source.
            # Supplying a nonmatching value is the invalid provenance case.
            mangled["authority_source"] = "caller_supplied_path"
        with pytest.raises(ValueError):
            RepositorySourceBinding.from_contract_payload(mangled)


def test_optional_source_manifest_digest_is_carried():
    digest = "d" * 64
    binding = _binding(source_manifest_sha256=digest, work_id="swarm-review-1")
    payload = binding.to_contract_payload()
    assert payload["source_manifest_sha256"] == digest
    assert payload["work_id"] == "swarm-review-1"
    with pytest.raises(ValueError):
        _binding(source_manifest_sha256="not-a-digest")


# --------------------------------------------------------------------------
# Verifier states
# --------------------------------------------------------------------------


def test_exact_requested_worktree_and_head_is_match():
    result = verify_repository_source(_binding(), _observed())
    assert result.state is SourceBindingState.MATCH
    assert result.accepted is True
    assert result.reasons == []


def test_same_repository_different_worktree_is_worktree_mismatch():
    result = verify_repository_source(
        _binding(), _observed(worktree_realpath="/srv/worktrees/auto-assist-other")
    )
    assert result.state is SourceBindingState.WORKTREE_MISMATCH
    assert result.accepted is False


def test_same_worktree_stale_head_is_head_mismatch():
    result = verify_repository_source(_binding(), _observed(head_sha=HEAD_B))
    assert result.state is SourceBindingState.HEAD_MISMATCH
    assert result.accepted is False


def test_different_mirror_with_same_repository_name_is_repository_mismatch():
    # A stale clone called "auto-assist" with the same basename and even the same
    # HEAD is still not the bound repository.
    result = verify_repository_source(
        _binding(), _observed(repository_realpath=STALE_MIRROR)
    )
    assert result.state is SourceBindingState.REPOSITORY_MISMATCH
    assert result.accepted is False


def test_missing_worktree_is_source_unavailable():
    result = verify_repository_source(
        _binding(), ObservedSource.unavailable("path_does_not_exist")
    )
    assert result.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert result.accepted is False


def test_dirty_worktree_rejected_when_clean_is_required():
    result = verify_repository_source(_binding(), _observed(dirty=True))
    assert result.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert result.accepted is False


def test_dirty_worktree_allowed_when_expected():
    binding = _binding(expected_dirty=DirtyStateExpectation.ANY)
    assert verify_repository_source(binding, _observed(dirty=True)).state is (
        SourceBindingState.MATCH
    )


def test_branch_mismatch_is_explicit():
    result = verify_repository_source(_binding(), _observed(branch="other-branch"))
    assert result.state is SourceBindingState.HEAD_MISMATCH


def test_branchless_binding_tolerates_detached_observed_branch():
    binding = _binding(branch="DETACHED")
    assert (
        verify_repository_source(binding, _observed(branch=None)).state
        is SourceBindingState.MATCH
    )


def test_non_repository_task_is_unaffected():
    result = verify_repository_source(None, _observed())
    assert result.state == "UNBOUND"
    assert result.accepted is True


def test_multiple_deviations_never_report_match():
    result = verify_repository_source(
        _binding(head_sha=HEAD_B), _observed(head_sha=HEAD_C, dirty=True)
    )
    assert result.accepted is False
    assert result.state is not SourceBindingState.MATCH


def test_verification_provenance_includes_expected_and_observed():
    provenance = verify_repository_source(_binding(), _observed()).to_provenance()
    assert provenance["state"] == "MATCH"
    assert provenance["expected"]["worktree_realpath"] == BOUND_WORKTREE
    assert provenance["observed"]["head_sha"] == HEAD_A


# --------------------------------------------------------------------------
# Observation: no search, no fallback
# --------------------------------------------------------------------------


def test_observe_reports_unavailable_for_non_repository_directory(tmp_path):
    plain = tmp_path / "embed_x1"
    plain.mkdir()
    observed = observe_source_workspace(str(plain))
    assert observed.available is False
    assert observed.unavailable_reason == "path_is_not_a_git_worktree"


def test_observe_reports_unavailable_for_missing_path(tmp_path):
    observed = observe_source_workspace(str(tmp_path / "absent"))
    assert observed.available is False
    assert observed.unavailable_reason == "path_does_not_exist"


def test_observe_reports_unavailable_for_blank_path():
    assert observe_source_workspace("").available is False


def test_verify_workspace_against_missing_path_fails_closed(tmp_path):
    result = verify_source_workspace(_binding(), str(tmp_path / "absent"))
    assert result.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert result.accepted is False
    # No substitute checkout was consulted or reported.
    assert result.observed.worktree_realpath is None


def test_verify_workspace_does_not_fall_back_to_home_when_target_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = verify_source_workspace(_binding(), str(tmp_path / "absent"))
    assert result.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert str(tmp_path) not in json.dumps(result.to_provenance())


# --------------------------------------------------------------------------
# Real git fixtures
# --------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def fleet_repo(tmp_path):
    """One repository with a main checkout plus two registered worktrees."""

    main = tmp_path / "auto-assist"
    main.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=main, check=True,
                   capture_output=True)
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "t")
    (main / "README.md").write_text("one\n", encoding="utf-8")
    _git(main, "add", "-A")
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-m", "one"], cwd=main, check=True,
                   capture_output=True)
    _git(main, "worktree", "add", "--detach", str(tmp_path / "wt-target"), "HEAD")
    # A second registered worktree of the same repository: right repo, wrong tree.
    _git(main, "worktree", "add", "--detach", str(tmp_path / "wt-other"), "HEAD")
    # A stale mirror: an independent clone with the same directory name.
    subprocess.run(
        ["git", "clone", "--quiet", str(main), str(tmp_path / "auto-assist-mirror")],
        check=True,
        capture_output=True,
    )
    return {
        "main": main,
        "target": tmp_path / "wt-target",
        "other": tmp_path / "wt-other",
        "mirror": tmp_path / "auto-assist-mirror",
        "root": tmp_path,
    }


def _bind(repo, worktree="target"):
    return build_source_binding(
        repository="auto-assist",
        worktree_path=repo[worktree],
        base_repository_path=repo["main"],
        task_id="task-1",
        work_id="work-1",
        env={"ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps(
            {"auto-assist": str(repo["main"].resolve())}
        )},
    )


def test_real_exact_worktree_and_head_is_accepted(fleet_repo):
    observed = observe_source_workspace(fleet_repo["target"])
    assert observed.available is True
    result = verify_repository_source(_bind(fleet_repo), observed)
    assert result.state is SourceBindingState.MATCH
    assert result.accepted is True


def test_real_same_repo_different_worktree_is_rejected(fleet_repo):
    result = verify_source_workspace(_bind(fleet_repo), str(fleet_repo["other"]))
    assert result.state is SourceBindingState.WORKTREE_MISMATCH
    assert result.accepted is False


def test_real_same_worktree_stale_head_is_rejected(fleet_repo):
    binding = _bind(fleet_repo)
    bound_root = Path(binding.worktree_realpath)
    _git(bound_root, "commit", "--allow-empty", "-m", "advance")
    result = verify_source_workspace(binding, str(bound_root))
    assert result.state is SourceBindingState.HEAD_MISMATCH
    assert result.accepted is False


def test_real_mirror_with_same_repo_name_is_rejected(fleet_repo):
    result = verify_source_workspace(_bind(fleet_repo), str(fleet_repo["mirror"]))
    assert result.state is SourceBindingState.REPOSITORY_MISMATCH
    assert result.accepted is False


def test_real_missing_worktree_is_rejected(fleet_repo):
    result = verify_source_workspace(_bind(fleet_repo), str(fleet_repo["root"] / "gone"))
    assert result.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert result.accepted is False


def test_build_binding_refuses_unregistered_sibling_directory(fleet_repo):
    impostor = fleet_repo["root"] / "auto-ingest-swarm-20261002"
    impostor.mkdir()
    with pytest.raises(ValueError):
        build_source_binding(
            repository="auto-assist",
            worktree_path=impostor,
            base_repository_path=fleet_repo["main"],
            task_id="task-1",
            work_id="work-1",
            env={"ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps(
                {"auto-assist": str(fleet_repo["main"].resolve())}
            )},
        )


def test_build_binding_refuses_mirror_as_worktree(fleet_repo):
    with pytest.raises(ValueError):
        build_source_binding(
            repository="auto-assist",
            worktree_path=fleet_repo["mirror"],
            base_repository_path=fleet_repo["main"],
            task_id="task-1",
            work_id="work-1",
            env={"ASSISTX_REPOSITORY_ROOTS_JSON": json.dumps(
                {"auto-assist": str(fleet_repo["main"].resolve())}
            )},
        )


def test_build_binding_binds_registered_worktree_of_same_repository(fleet_repo):
    binding = _bind(fleet_repo, worktree="other")
    assert binding.worktree_realpath == str(fleet_repo["other"].resolve())
    assert binding.repo_realpath == str(fleet_repo["main"].resolve())


# --------------------------------------------------------------------------
# Task/delegation contract wiring
# --------------------------------------------------------------------------


def _contract(**overrides):
    values = {
        "repository": "auto-assist",
        "objective": "Add one focused regression test",
        "allowed_paths": ["tests/test_example.py"],
        "verification_commands": [["pytest", "-q", "tests/test_example.py"]],
    }
    values.update(overrides)
    return build_execution_contract(**values)


def test_contract_without_binding_is_unchanged_for_legacy_tasks():
    assert "source_binding" not in _contract()


def test_contract_with_binding_rejects_foreign_repository_identity():
    with pytest.raises(ValueError):
        _contract(repository="auto-router", source_binding=_binding())


def test_work_packet_carries_binding_and_requirement():
    packet = build_work_packet(
        {
            "id": "task-1",
            "title": "t",
            "payload_json": json.dumps(
                {"execution_contract": _contract(source_binding=_binding())}
            ),
        }
    )
    assert packet["source_binding"]["worktree_realpath"] == BOUND_WORKTREE
    assert "another checkout" in packet["source_binding_requirement"]


def _envelope(state: str | None) -> dict:
    patch = "diff --git a/tests/test_example.py b/tests/test_example.py\n"
    unsigned = {
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
        "patch": patch,
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "summary": "done",
    }
    if state is not None:
        unsigned["source_binding_verification"] = {
            "state": state,
            "accepted": state == "MATCH",
            "reasons": [] if state == "MATCH" else ["worktree_realpath_mismatch"],
            "observed": {},
        }
    return sign_executor_evidence(unsigned, key_id="k1", secret="s3cret")


def _task(binding=None):
    contract = _contract(source_binding=binding) if binding else _contract()
    return {
        "id": "task-1",
        "title": "t",
        "kind": "bounded_code_change",
        "payload_json": json.dumps({"execution_contract": contract}),
    }


def _evaluate(task, state):
    return evaluate_completion(
        task,
        requested_status="DONE",
        result={"completion_envelope": _envelope(state)},
        verify_keys={"k1": "s3cret"},
    )


def test_bound_completion_requires_matching_source_provenance():
    assert _evaluate(_task(_binding()), "MATCH")["accepted"] is True


def test_bound_completion_rejected_when_provenance_missing():
    evaluation = _evaluate(_task(_binding()), None)
    assert evaluation["accepted"] is False
    assert "repository_source_binding_unverified" in evaluation["reasons"]


def test_bound_completion_rejected_when_reviewer_used_wrong_mirror():
    evaluation = _evaluate(_task(_binding()), "REPOSITORY_MISMATCH")
    assert evaluation["accepted"] is False
    assert (
        "repository_source_binding_rejected:REPOSITORY_MISMATCH"
        in evaluation["reasons"]
    )


def test_bound_completion_rejected_when_reviewer_used_stale_head():
    evaluation = _evaluate(_task(_binding()), "HEAD_MISMATCH")
    assert evaluation["accepted"] is False
    assert "repository_source_binding_rejected:HEAD_MISMATCH" in evaluation["reasons"]


def test_unbound_completion_is_unaffected_by_the_gate():
    assert _evaluate(_task(), "REPOSITORY_MISMATCH")["accepted"] is True
