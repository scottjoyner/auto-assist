"""Round-43 regression: an unreadable dirty state is not proof of a clean tree.

The derived-workspace verifier certifies isolated execution worktrees.  It must
not report ``MATCH`` when git's dirty state could not be read (``None``): that
is the same "cannot observe -> accepted" failure the canonical verifier already
fails closed on for strict clean expectations.

Pure in-process test: no git subprocess, socket, HTTP, Redis, Neo4j, SQLite,
DB/WAL, credential or live outbox access.  All identity values are inert
fixtures; no path in this file is read or written.
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

REPO = "/opt/assistx-r43/repo"
WORKTREE = "/opt/assistx-r43/worktree"
HEAD = "8c42cf1a65a6229dd97de05faf8ba81e3ef8d628"


def _binding(
    expected_dirty: DirtyStateExpectation = DirtyStateExpectation.CLEAN,
) -> RepositorySourceBinding:
    return RepositorySourceBinding(
        repository="assistx",
        repo_realpath=REPO,
        worktree_realpath=WORKTREE,
        branch="DETACHED",
        head_sha=HEAD,
        expected_dirty=expected_dirty,
        task_id="t-r43",
        work_id="w-r43",
    )


def _observed(dirty: bool | None, **overrides: object) -> ObservedSourceState:
    state = {
        "available": True,
        "repo_realpath": REPO,
        "worktree_realpath": WORKTREE,
        "branch": "DETACHED",
        "head_sha": HEAD,
        "dirty": dirty,
    }
    state.update(overrides)
    return ObservedSourceState(**state)


@pytest.mark.parametrize(
    "expected",
    [
        DirtyStateExpectation.CLEAN,
        DirtyStateExpectation.CLEAN_REQUIRED,
        DirtyStateExpectation.ANY,
    ],
)
def test_unreadable_dirty_state_is_rejected(expected: DirtyStateExpectation) -> None:
    verdict = verify_derived_source(_binding(expected), _observed(None))
    assert verdict.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert verdict.accepted is False
    assert verdict.reasons


def test_known_dirty_is_still_rejected() -> None:
    verdict = verify_derived_source(_binding(), _observed(True))
    assert verdict.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert verdict.accepted is False


def test_provably_clean_is_still_accepted() -> None:
    verdict = verify_derived_source(_binding(), _observed(False))
    assert verdict.state is SourceBindingState.MATCH
    assert verdict.accepted is True


def test_unavailable_source_is_rejected_before_dirty_check() -> None:
    verdict = verify_derived_source(_binding(), _observed(None, available=False))
    assert verdict.state is SourceBindingState.SOURCE_UNAVAILABLE
    assert verdict.accepted is False


def test_repository_mismatch_is_still_rejected() -> None:
    verdict = verify_derived_source(_binding(), _observed(None, repo_realpath="/opt/other/repo"))
    assert verdict.state is SourceBindingState.REPOSITORY_MISMATCH
    assert verdict.accepted is False


def test_head_mismatch_is_still_rejected() -> None:
    verdict = verify_derived_source(_binding(), _observed(None, head_sha="0" * 40))
    assert verdict.state is SourceBindingState.HEAD_MISMATCH
    assert verdict.accepted is False


def test_acceptance_grants_no_authority() -> None:
    verdict = verify_derived_source(_binding(), _observed(False))
    assert verdict.dispatch_allowed is False
    assert verdict.approval_granted is False
    assert verdict.claim_acquired is False
    assert verdict.mutation_allowed is False
    assert verdict.execution_authority_granted is False
    assert verdict.routing_authority_changed is False
    assert verdict.fallback_candidates_considered == []


def test_sibling_verifier_still_rejects_unreadable_clean_required() -> None:
    """Control: r13's adjacent CLEAN_REQUIRED fix is untouched by this change."""
    verdict = verify_source_binding(_binding(DirtyStateExpectation.CLEAN_REQUIRED), _observed(None))
    assert verdict.state is SourceBindingState.DIRTY_STATE_MISMATCH
    assert verdict.accepted is False
