"""Fail-closed verification of repository source bindings.

This module answers one question: *did the workspace actually used match the
source that was pinned for this task?* It answers it empirically, by observing
the workspace, and it fails closed.

No fallback, ever
-----------------
On any mismatch this module returns a rejection. It does **not**:

* resolve a different checkout of the same repository,
* fall back to ``$HOME``,
* search by repository name,
* accept ``/home/scott/embed_x1`` or any other stale mirror,
* substitute another worktree belonging to the same repository.

A fallback is the defect, not the remedy. If the pinned source is unavailable,
the correct answer is ``SOURCE_UNAVAILABLE`` and the caller decides; silently
examining some other directory is precisely how a stale review reaches a
confident wrong verdict. :attr:`SourceBindingVerification.fallback_attempted` is
always ``False`` and exists so tests can pin that invariant.

This module grants no authority. It produces evidence about source identity and
nothing else.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from assistx.contracts.schemas.repository_source_binding import (
    SOURCE_BINDING_SCHEMA,
    DirtyStateExpectation,
    RepositorySourceBinding,
)

GIT_TIMEOUT_SECONDS = 30


class SourceBindingState(str, Enum):
    """Verdict of comparing an observed workspace against an expected binding."""

    MATCH = "MATCH"
    WORKTREE_MISMATCH = "WORKTREE_MISMATCH"
    HEAD_MISMATCH = "HEAD_MISMATCH"
    REPOSITORY_MISMATCH = "REPOSITORY_MISMATCH"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    DIRTY_STATE_MISMATCH = "DIRTY_STATE_MISMATCH"


@dataclass(frozen=True)
class SourceObservation:
    """What is empirically true about a workspace right now."""

    available: bool
    worktree_realpath: str | None = None
    repository_realpath: str | None = None
    head_sha: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    detail: str = ""


@dataclass(frozen=True)
class SourceBindingVerification:
    """Outcome of verifying an observed workspace against an expected binding."""

    state: SourceBindingState
    expected: RepositorySourceBinding
    observed: SourceObservation
    reasons: list[str] = field(default_factory=list)
    fallback_attempted: bool = False

    @property
    def matched(self) -> bool:
        return self.state is SourceBindingState.MATCH

    @property
    def grants_authority(self) -> bool:
        """Always False. A source binding is evidence, never a grant."""
        return False


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return 1, "", f"git invocation failed: {error}"
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def observe_source(worktree: Any) -> SourceObservation:
    """Observe a workspace empirically.

    Nothing here trusts the caller's label for the path: every fact comes from
    ``git`` running inside the directory itself. A mirror, a stale checkout, and
    the authoritative worktree are indistinguishable by name and are told apart
    only by these observations.
    """
    try:
        root = Path(str(worktree)).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as error:
        return SourceObservation(available=False, detail=f"unresolvable path: {error}")
    if not root.is_dir():
        return SourceObservation(
            available=False,
            worktree_realpath=str(root),
            detail="worktree path does not exist",
        )

    code, toplevel, _ = _git(["rev-parse", "--show-toplevel"], root)
    if code != 0 or not toplevel:
        return SourceObservation(
            available=False,
            worktree_realpath=str(root),
            detail="worktree is not a git checkout",
        )
    worktree_realpath = str(Path(toplevel).resolve())

    # The canonical repository is the checkout owning the shared git dir, not
    # this worktree. Two worktrees of one repository share it; an unrelated
    # mirror with the same project name does not.
    code, common_dir, _ = _git(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"], root
    )
    repository_realpath: str | None = None
    if code == 0 and common_dir:
        git_dir = Path(common_dir).resolve()
        repository_realpath = (
            str(git_dir.parent) if git_dir.name == ".git" else str(git_dir)
        )

    code, head, _ = _git(["rev-parse", "HEAD"], root)
    head_sha = head.lower() if code == 0 and head else None

    code, branch, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    branch_name = branch if code == 0 and branch else "DETACHED"

    code, status, _ = _git(["status", "--porcelain", "--untracked-files=all"], root)
    dirty = bool(status) if code == 0 else None

    return SourceObservation(
        available=True,
        worktree_realpath=worktree_realpath,
        repository_realpath=repository_realpath,
        head_sha=head_sha,
        branch=branch_name,
        dirty=dirty,
    )


def _registry_roots(environment: dict[str, str]) -> dict[str, str]:
    try:
        roots = json.loads(environment.get("ASSISTX_REPOSITORY_ROOTS_JSON", "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(roots, dict):
        return {}
    return {str(key): str(value) for key, value in roots.items() if value}


def bind_repository_source(
    repository: str,
    worktree: Any,
    *,
    work_id: str,
    expect_dirty: DirtyStateExpectation = DirtyStateExpectation.CLEAN,
    source_manifest_sha256: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[RepositorySourceBinding | None, str]:
    """Mint a binding for a registry-declared repository, or explain refusal.

    ``worktree`` selects *which* registered repository root to inspect; it is
    never adopted as an identity by itself. Identity comes from ``git`` inside
    that root. A repository absent from ``ASSISTX_REPOSITORY_ROOTS_JSON`` is
    refused outright, so an arbitrary host path cannot be promoted to a binding
    by a caller.
    """
    environment = env if env is not None else dict(os.environ)
    roots = _registry_roots(environment)
    if repository not in roots:
        return None, "repository_root_not_configured"

    observed = observe_source(worktree)
    if not observed.available or observed.repository_realpath is None:
        return None, observed.detail or "source_unavailable"

    registered = Path(roots[repository]).expanduser().resolve()
    if observed.repository_realpath != str(registered):
        # Same project name, different checkout. Refuse rather than bind.
        return None, "registered_repository_root_mismatch"

    try:
        binding = RepositorySourceBinding(
            repository=repository,
            repository_realpath=observed.repository_realpath,
            worktree_realpath=observed.worktree_realpath or str(registered),
            branch=observed.branch or "DETACHED",
            head_sha=observed.head_sha or "",
            expect_dirty=expect_dirty,
            work_id=work_id,
            source_manifest_sha256=source_manifest_sha256,
        )
    except ValueError as error:
        return None, f"binding_invalid:{error}"
    return binding, ""


def verify_source_binding(
    expected: RepositorySourceBinding,
    observed: SourceObservation,
) -> SourceBindingVerification:
    """Compare an observed workspace against the expected binding, fail-closed.

    Precedence is fixed so a single cause is always reported first:
    ``SOURCE_UNAVAILABLE`` > ``REPOSITORY_MISMATCH`` > ``WORKTREE_MISMATCH`` >
    ``HEAD_MISMATCH`` > ``DIRTY_STATE_MISMATCH``. Dirty state is only judged once
    identity matches, because "different directory" is more actionable than
    "uncommitted changes".
    """

    def reject(state: SourceBindingState, reason: str) -> SourceBindingVerification:
        return SourceBindingVerification(
            state=state,
            expected=expected,
            observed=observed,
            reasons=[reason],
            fallback_attempted=False,
        )

    if not observed.available:
        return reject(
            SourceBindingState.SOURCE_UNAVAILABLE,
            observed.detail or "expected source is unavailable",
        )
    if observed.repository_realpath is None:
        return reject(
            SourceBindingState.SOURCE_UNAVAILABLE,
            "could not determine the repository owning the observed workspace",
        )
    if observed.repository_realpath != expected.repository_realpath:
        return reject(
            SourceBindingState.REPOSITORY_MISMATCH,
            "observed repository is not the pinned repository",
        )
    if observed.worktree_realpath != expected.worktree_realpath:
        return reject(
            SourceBindingState.WORKTREE_MISMATCH,
            "observed worktree is not the pinned worktree; "
            "another checkout of the same repository is not a substitute",
        )
    if (observed.head_sha or "") != expected.head_sha:
        return reject(
            SourceBindingState.HEAD_MISMATCH,
            "observed HEAD is not the pinned HEAD",
        )
    if expected.expect_dirty is not DirtyStateExpectation.ANY:
        if observed.dirty is None:
            return reject(
                SourceBindingState.SOURCE_UNAVAILABLE,
                "could not determine the dirty state of the observed worktree",
            )
        if expected.expect_dirty is DirtyStateExpectation.CLEAN and observed.dirty:
            return reject(
                SourceBindingState.DIRTY_STATE_MISMATCH,
                "bound worktree was expected clean but has uncommitted changes",
            )
        if expected.expect_dirty is DirtyStateExpectation.DIRTY and not observed.dirty:
            return reject(
                SourceBindingState.DIRTY_STATE_MISMATCH,
                "bound worktree was expected dirty but is clean",
            )

    return SourceBindingVerification(
        state=SourceBindingState.MATCH,
        expected=expected,
        observed=observed,
        reasons=[
            "repository realpath, worktree realpath and HEAD all match the binding",
        ],
        fallback_attempted=False,
    )


def binding_to_document(binding: RepositorySourceBinding) -> dict[str, Any]:
    """Serialize a binding for provenance on a task or result."""
    document = binding.model_dump(mode="json")
    document["schema_version"] = SOURCE_BINDING_SCHEMA
    return document


def document_to_binding(document: dict[str, Any]) -> RepositorySourceBinding:
    """Rebuild a binding from a provenance document, fail-closed."""
    return RepositorySourceBinding.model_validate(document)
