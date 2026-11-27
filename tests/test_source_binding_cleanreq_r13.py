"""CLEAN_REQUIRED is a hard clean-tree requirement, not a no-op (r13).

The swarm-side verifier (``assistx.repository_source_verifier``) shares its
``DirtyStateExpectation`` enum with the contracts verifier, including the
``CLEAN_REQUIRED`` member.  The contracts verifier rejects a dirty or unreadable
worktree for that expectation; the swarm verifier used to accept it as MATCH.
These tests pin the fail-closed meaning without any filesystem, git or network
access -- both sides are constructed in memory.
"""

from __future__ import annotations

import pytest

from assistx.contracts.schemas.repository_source_binding import (
    DirtyStateExpectation,
    ObservedSourceState,
    RepositorySourceBinding,
    SourceBindingState,
)
from assistx.repository_source_verifier import (
    verify_derived_source,
    verify_source_binding,
)

REPO = "/srv/repos/auto-assist"
WORKTREE = "/srv/worktrees/auto-ingest-swarm-r13"
HEAD = "a" * 40


def _binding(dirty_expectation: DirtyStateExpectation) -> RepositorySourceBinding:
    return RepositorySourceBinding(
        repository="auto-assist",
        repo_realpath=REPO,
        worktree_realpath=WORKTREE,
        branch="main",
        head_sha=HEAD,
        expected_dirty=dirty_expectation,
        task_id="task-r13",
        work_id="work-r13",
    )


def _observed(dirty: bool | None) -> ObservedSourceState:
    return ObservedSourceState(
        available=True,
        repo_realpath=REPO,
        worktree_realpath=WORKTREE,
        branch="main",
        head_sha=HEAD,
        dirty=dirty,
    )


def test_clean_required_rejects_dirty_worktree() -> None:
    """The regression: CLEAN_REQUIRED used to be silently ignored."""
    verdict = verify_source_binding(
        _binding(DirtyStateExpectation.CLEAN_REQUIRED), _observed(True)
    )
    assert verdict.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert verdict.accepted is False


def test_clean_required_rejects_unreadable_dirty_state() -> None:
    """'Could not check dirtiness' must not be reported as clean."""
    verdict = verify_source_binding(
        _binding(DirtyStateExpectation.CLEAN_REQUIRED), _observed(None)
    )
    assert verdict.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert verdict.accepted is False


def test_clean_required_accepts_clean_worktree() -> None:
    verdict = verify_source_binding(
        _binding(DirtyStateExpectation.CLEAN_REQUIRED), _observed(False)
    )
    assert verdict.state is SourceBindingState.MATCH
    assert verdict.accepted is True


def test_legacy_clean_expectation_still_rejects_dirty() -> None:
    verdict = verify_source_binding(_binding(DirtyStateExpectation.CLEAN), _observed(True))
    assert verdict.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert verdict.accepted is False


def test_dirty_expectation_still_accepts_dirty() -> None:
    verdict = verify_source_binding(_binding(DirtyStateExpectation.DIRTY), _observed(True))
    assert verdict.state is SourceBindingState.MATCH


def test_any_expectation_still_accepts_dirty() -> None:
    verdict = verify_source_binding(_binding(DirtyStateExpectation.ANY), _observed(True))
    assert verdict.state is SourceBindingState.MATCH


def test_derived_source_still_rejects_dirty_regardless_of_expectation() -> None:
    """Control: the derived-worktree path was already fail-closed."""
    verdict = verify_derived_source(
        _binding(DirtyStateExpectation.ANY), _observed(True)
    )
    assert verdict.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert verdict.accepted is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
