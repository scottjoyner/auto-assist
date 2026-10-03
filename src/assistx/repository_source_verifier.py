"""Fail-closed verification of an observed workspace against a source binding.

This module answers one question: *did the worker that produced this result
actually read the tree the task named?* It never answers a weaker question like
"was some checkout of the right repository available?".

No fallback exists here, by construction. Given a workspace path, this module
observes exactly that path. If it is missing, unreadable, or not a git
checkout, the answer is ``SOURCE_UNAVAILABLE``. It does not:

* search for another checkout of the same repository name;
* consult ``$HOME``;
* consider a sibling worktree of the same repository;
* substitute the stale ``/home/scott/embed_x1`` mirror or any other path.

A previous swarm reviewer inspected ``/home/scott/embed_x1`` instead of the
authoritative ``/media/scott/SSD_4TB/worktrees/auto-ingest-swarm-20261002``
worktree, and the resulting review had to be rejected. Every one of those
behaviours is exactly what made that incident unrecoverable, so none of them is
implemented.

The verifier holds no authority. It reads git state and returns a verdict; it
never claims, executes, writes, dispatches, approves, or routes.

Importing this module runs ``assistx/__init__.py``, which installs the runtime
safety boundaries. Use it from inside AssistX, or load the contract module
directly as described in
``contracts/schemas/repository_source_binding.py`` when only the contract is
needed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts.schemas.repository_source_binding import (
    DirtyStateExpectation,
    ObservedSourceState,
    RepositorySourceBinding,
    SourceBindingState,
    SourceBindingVerdict,
)

GIT_TIMEOUT_SECONDS = 30
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
REPOSITORY_ROOTS_ENV = "ASSISTX_REPOSITORY_ROOTS_JSON"

#: The concrete stale mirror from the incident this contract exists to prevent.
#: Named only so it can be asserted against in tests; never used as a fallback.
FORBIDDEN_MIRROR = "/home/scott/embed_x1"


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run one git command, returning (returncode, stdout).

    Distinguishing a non-zero return from empty output matters: an earlier
    reader collapsed "git failed" into "value is empty", which made a broken
    repository look like a clean one.
    """

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return result.returncode, result.stdout.strip()


def configured_repository_roots(
    env: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    """Resolve the operator-controlled repository alias map.

    This is the only source of repository identity. An alias absent from this
    map has no authority, so a caller cannot invent one.
    """

    environment = env if env is not None else os.environ
    raw = str(environment.get(REPOSITORY_ROOTS_ENV, "") or "").strip()
    if not raw:
        return {}
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(document, dict):
        return {}

    roots: dict[str, Path] = {}
    for alias, value in document.items():
        path_value: Any = value
        if isinstance(value, dict):
            path_value = value.get("path") or value.get("root")
        name = str(alias).strip()
        text = str(path_value or "").strip()
        if not name or not text or text.startswith("~"):
            continue
        try:
            roots[name] = Path(text).resolve()
        except OSError:
            continue
    return roots


def _repository_root_for(worktree: Path) -> Path | None:
    """Return the root of the repository that owns ``worktree``.

    ``git rev-parse --git-common-dir`` resolves through a worktree link back to
    the shared repository, which is what makes "same repository, different
    worktree" distinguishable from "same repository, same worktree".
    """

    code, value = _git(["rev-parse", "--git-common-dir"], worktree)
    if code != 0 or not value:
        return None
    common = Path(value)
    if not common.is_absolute():
        common = (worktree / common).resolve()
    try:
        return common.parent.resolve()
    except OSError:
        return None


def observe_source(path: str | Path | None) -> ObservedSourceState:
    """Observe exactly one workspace path.

    Never searches, never substitutes. Returns ``ObservedSourceState.unavailable``
    for anything that is not a readable git worktree with a resolvable HEAD.
    """

    if path is None or not str(path).strip():
        return ObservedSourceState.unavailable("no_workspace_path_supplied")
    # A hostile path (embedded NUL, over-long component, bad encoding) must fail
    # closed like any other unusable source, not raise out of the verifier.
    if _CONTROL.search(str(path)):
        return ObservedSourceState.unavailable("workspace_path_contains_control_characters")
    try:
        candidate = Path(str(path)).resolve()
    except (OSError, ValueError):
        return ObservedSourceState.unavailable("workspace_path_unresolvable")
    if not candidate.is_dir():
        return ObservedSourceState.unavailable("workspace_path_is_not_a_directory")

    code, toplevel = _git(["rev-parse", "--show-toplevel"], candidate)
    if code != 0 or not toplevel:
        return ObservedSourceState.unavailable("workspace_is_not_a_git_worktree")
    worktree_realpath = Path(toplevel).resolve()

    repo_realpath = _repository_root_for(worktree_realpath)
    if repo_realpath is None:
        return ObservedSourceState.unavailable("repository_common_dir_unresolvable")

    code, head = _git(["rev-parse", "HEAD"], worktree_realpath)
    if code != 0 or not head:
        return ObservedSourceState.unavailable("git_head_unreadable")

    code, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], worktree_realpath)
    resolved_branch = branch if code == 0 and branch else "DETACHED"

    code, status = _git(["status", "--porcelain"], worktree_realpath)
    if code != 0:
        return ObservedSourceState.unavailable("git_status_unreadable")

    return ObservedSourceState(
        available=True,
        repo_realpath=str(repo_realpath),
        worktree_realpath=str(worktree_realpath),
        branch=resolved_branch,
        head_sha=head,
        dirty=bool(status),
    )


def build_binding(
    *,
    repository: str,
    worktree_path: str | Path,
    task_id: str,
    work_id: str,
    expected_dirty: DirtyStateExpectation = DirtyStateExpectation.CLEAN,
    source_manifest_sha256: str | None = None,
    env: Mapping[str, str] | None = None,
) -> RepositorySourceBinding:
    """Construct a binding whose repository identity came from configuration.

    Raises when the alias is not configured or when the observed worktree does
    not belong to the repository that alias points at. Failing here is
    deliberate: a binding nobody can verify is worse than no binding.
    """

    roots = configured_repository_roots(env)
    configured = roots.get(str(repository).strip())
    if configured is None:
        raise ValueError(f"repository alias is not configured: {repository!r}")

    observed = observe_source(worktree_path)
    if not observed.available:
        raise ValueError(
            f"cannot bind unavailable worktree: {observed.unavailable_reason}"
        )
    if observed.repo_realpath != str(configured):
        raise ValueError(
            "worktree does not belong to the configured repository root for "
            f"{repository!r}"
        )

    return RepositorySourceBinding(
        repository=str(repository).strip(),
        repo_realpath=observed.repo_realpath or "",
        worktree_realpath=observed.worktree_realpath or "",
        branch=observed.branch or "DETACHED",
        head_sha=observed.head_sha or "",
        expected_dirty=expected_dirty,
        task_id=task_id,
        work_id=work_id,
        source_manifest_sha256=source_manifest_sha256,
    )


def _verdict(
    binding: RepositorySourceBinding,
    observed: ObservedSourceState,
    state: SourceBindingState,
    reasons: list[str],
) -> SourceBindingVerdict:
    return SourceBindingVerdict(
        state=state,
        task_id=binding.task_id,
        work_id=binding.work_id,
        repository=binding.repository,
        expected_repo_realpath=binding.repo_realpath,
        expected_worktree_realpath=binding.worktree_realpath,
        expected_branch=binding.branch,
        expected_head_sha=binding.head_sha,
        observed_repo_realpath=observed.repo_realpath,
        observed_worktree_realpath=observed.worktree_realpath,
        observed_branch=observed.branch,
        observed_head_sha=observed.head_sha,
        observed_dirty=observed.dirty,
        reasons=reasons,
    )


def verify_source_binding(
    binding: RepositorySourceBinding,
    observed: ObservedSourceState,
) -> SourceBindingVerdict:
    """Compare one observed workspace against an expected binding.

    Checked in a fixed order, most-fundamental identity first, so the reported
    state names the real problem rather than a downstream symptom. Any failure
    is a rejection; there is no partial acceptance and no fallback.
    """

    if not observed.available:
        return _verdict(
            binding,
            observed,
            SourceBindingState.SOURCE_UNAVAILABLE,
            [str(observed.unavailable_reason or "source_unavailable")],
        )

    if observed.repo_realpath != binding.repo_realpath:
        return _verdict(
            binding,
            observed,
            SourceBindingState.REPOSITORY_MISMATCH,
            [
                "observed repository does not match the bound repository "
                f"({observed.repo_realpath} != {binding.repo_realpath})"
            ],
        )

    if observed.worktree_realpath != binding.worktree_realpath:
        return _verdict(
            binding,
            observed,
            SourceBindingState.WORKTREE_MISMATCH,
            [
                "observed worktree is not the bound worktree "
                f"({observed.worktree_realpath} != {binding.worktree_realpath}); "
                "a sibling worktree or stale mirror is not an accepted substitute"
            ],
        )

    if binding.branch != "DETACHED" and observed.branch != binding.branch:
        return _verdict(
            binding,
            observed,
            SourceBindingState.HEAD_MISMATCH,
            [
                f"observed branch {observed.branch} does not match bound branch "
                f"{binding.branch}"
            ],
        )

    if observed.head_sha != binding.head_sha:
        return _verdict(
            binding,
            observed,
            SourceBindingState.HEAD_MISMATCH,
            [
                f"observed HEAD {observed.head_sha} does not match bound HEAD "
                f"{binding.head_sha}; the checkout is stale"
            ],
        )

    if binding.expected_dirty is DirtyStateExpectation.CLEAN and observed.dirty:
        return _verdict(
            binding,
            observed,
            SourceBindingState.DIRTY_STATE_MISMATCH,
            ["bound worktree was required to be clean but has local modifications"],
        )
    if binding.expected_dirty is DirtyStateExpectation.DIRTY and not observed.dirty:
        return _verdict(
            binding,
            observed,
            SourceBindingState.DIRTY_STATE_MISMATCH,
            ["bound worktree was required to have local modifications but is clean"],
        )

    return _verdict(binding, observed, SourceBindingState.MATCH, [])


def verify_workspace(
    binding: RepositorySourceBinding,
    workspace_path: str | Path | None,
) -> SourceBindingVerdict:
    """Observe one named workspace and verify it. Never searches."""

    return verify_source_binding(binding, observe_source(workspace_path))


def binding_from_payload(payload: Mapping[str, Any] | None) -> RepositorySourceBinding | None:
    """Parse a binding out of a task or delegation payload.

    Returns ``None`` when no binding is present, which keeps non-repository
    tasks unaffected. Raises when a binding *is* present but malformed: a
    corrupt binding must never be silently downgraded to "unbound" and thereby
    skip verification.
    """

    if payload is None:
        return None
    raw = payload.get("source_binding") if isinstance(payload, Mapping) else None
    if raw is None:
        return None
    if isinstance(raw, RepositorySourceBinding):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("source_binding must be an object")
    if not raw:
        return None
    return RepositorySourceBinding.model_validate(dict(raw))


__all__ = [
    "FORBIDDEN_MIRROR",
    "REPOSITORY_ROOTS_ENV",
    "binding_from_payload",
    "build_binding",
    "configured_repository_roots",
    "observe_source",
    "verify_source_binding",
    "verify_workspace",
]
