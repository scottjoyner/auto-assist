"""Fail-closed verification of an observed workspace against a source binding.

The verifier compares exactly three facts on each side:

* repository realpath (the main checkout that owns the object database)
* worktree realpath (the exact tree a worker reads or writes)
* HEAD commit

plus optional branch and dirty-state expectations.

It never searches for a substitute source. A mismatch is a rejection, not a
prompt to look elsewhere: no other checkout of the same repository, no
repository-name search, no ``$HOME`` guess, no fallback mirror. When the
requested source cannot be observed at all the answer is
``SOURCE_UNAVAILABLE``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .schemas.repository_source_binding import (
    DirtyStateExpectation,
    RepositorySourceBinding,
    SourceBindingState,
)

__all__ = [
    "ObservedSource",
    "SourceBindingVerification",
    "build_source_binding",
    "observe_source_workspace",
    "verify_repository_source",
    "verify_source_workspace",
]


class ObservedSource(BaseModel):
    """Facts read from a candidate workspace. Never inferred, never guessed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    available: bool = Field(
        ..., description="False when the candidate could not be inspected at all."
    )
    worktree_realpath: str | None = None
    repository_realpath: str | None = None
    head_sha: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    unavailable_reason: str | None = None

    @classmethod
    def unavailable(cls, reason: str) -> ObservedSource:
        return cls(available=False, unavailable_reason=str(reason))

    def to_provenance(self) -> dict[str, Any]:
        """Serialize for task/result provenance."""

        return self.model_dump(mode="json", exclude_none=True)


class SourceBindingVerification(BaseModel):
    """Verdict for one (binding, observed) comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SourceBindingState
    accepted: bool
    reasons: list[str] = Field(default_factory=list)
    repository: str | None = None
    expected: RepositorySourceBinding | None = None
    observed: ObservedSource

    def to_provenance(self) -> dict[str, Any]:
        """Serialize for task/result provenance (expected binding included)."""

        payload: dict[str, Any] = {
            "state": self.state.value,
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "repository": self.repository,
            "observed": self.observed.to_provenance(),
        }
        if self.expected is not None:
            payload["expected"] = self.expected.to_contract_payload()
        return payload


def verify_repository_source(
    binding: RepositorySourceBinding | None,
    observed: ObservedSource,
) -> SourceBindingVerification:
    """Compare an observed workspace against the expected binding.

    Pure comparison -- no filesystem or git access happens here. Explicit
    unbound tasks are handled by callers, not accepted by a bound-source
    verifier. Missing binding is not an admission grant.
    """

    if binding is None:
        return SourceBindingVerification(
            state=SourceBindingState.SOURCE_UNAVAILABLE,
            accepted=False,
            reasons=["no_repository_source_binding"],
            observed=observed,
        )

    if not observed.available:
        return SourceBindingVerification(
            state=SourceBindingState.SOURCE_UNAVAILABLE,
            accepted=False,
            reasons=[
                f"source_unavailable:{observed.unavailable_reason or 'unknown'}"
            ],
            repository=binding.repository,
            expected=binding,
            observed=observed,
        )

    # Repository identity first: a mirror/clone of the "same repository" is a
    # different repository as far as source identity is concerned.
    if not _same_path(observed.repository_realpath, binding.repo_realpath):
        return _reject(
            SourceBindingState.REPOSITORY_MISMATCH,
            binding,
            observed,
            "repository_realpath_mismatch",
        )

    if not _same_path(observed.worktree_realpath, binding.worktree_realpath):
        return _reject(
            SourceBindingState.WORKTREE_MISMATCH,
            binding,
            observed,
            "worktree_realpath_mismatch",
        )

    if binding.branch != "DETACHED" and observed.branch != binding.branch:
        return _reject(
            SourceBindingState.HEAD_MISMATCH,
            binding,
            observed,
            "branch_mismatch",
        )

    if not _same_head(observed.head_sha, binding.head_sha):
        return _reject(
            SourceBindingState.HEAD_MISMATCH, binding, observed, "head_sha_mismatch"
        )

    if (
        binding.expected_dirty in (DirtyStateExpectation.CLEAN, DirtyStateExpectation.CLEAN_REQUIRED)
        and observed.dirty is not False
    ) or (
        binding.expected_dirty is DirtyStateExpectation.DIRTY
        and observed.dirty is not True
    ) or observed.dirty is None:
        return _reject(
            SourceBindingState.DIRTY_STATE_MISMATCH,
            binding,
            observed,
            "dirty_state_not_as_expected",
        )

    return SourceBindingVerification(
        state=SourceBindingState.MATCH,
        accepted=True,
        reasons=[],
        repository=binding.repository,
        expected=binding,
        observed=observed,
    )


def verify_source_workspace(
    binding: RepositorySourceBinding | None,
    worktree_path: Any,
) -> SourceBindingVerification:
    """Observe ``worktree_path`` and verify it against ``binding``.

    This is the entry point for reviewers and executors. It reads only the
    requested path; it never searches for a replacement checkout.
    """

    observed = observe_source_workspace(worktree_path)
    if (
        binding is not None
        and observed.available
        and _same_path(observed.worktree_realpath, binding.worktree_realpath)
    ):
        # Re-check dirtiness in the bound worktree itself so a clean-but-elsewhere
        # observation cannot satisfy a CLEAN_REQUIRED expectation.
        observed = observed.model_copy(
            update={"dirty": _read_dirty(observed.worktree_realpath)}
        )
    return verify_repository_source(binding, observed)


def observe_source_workspace(candidate: Any) -> ObservedSource:
    """Read repository/worktree/HEAD facts for ``candidate``.

    Returns an unavailable observation when the path is missing, is not a git
    worktree, or git cannot answer. Never substitutes a different path.
    """

    if candidate is None or str(candidate).strip() == "":
        return ObservedSource.unavailable("no_path_supplied")
    raw = str(candidate)
    if "\x00" in raw:
        return ObservedSource.unavailable("path_contains_control_characters")

    try:
        resolved = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return ObservedSource.unavailable("path_not_resolvable")
    if not resolved.exists():
        return ObservedSource.unavailable("path_does_not_exist")
    if not resolved.is_dir():
        return ObservedSource.unavailable("path_is_not_a_directory")

    toplevel = _git_text(["rev-parse", "--show-toplevel"], str(resolved))
    if not toplevel:
        return ObservedSource.unavailable("path_is_not_a_git_worktree")
    head = _git_text(["rev-parse", "HEAD"], str(resolved))
    if not head:
        return ObservedSource.unavailable("git_head_unreadable")
    common = _git_text(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"], str(resolved)
    )
    repository_realpath = _repository_root(resolved, common)
    if not repository_realpath:
        return ObservedSource.unavailable("repository_root_unreadable")
    branch = _git_text(["rev-parse", "--abbrev-ref", "HEAD"], str(resolved))
    return ObservedSource(
        available=True,
        worktree_realpath=str(resolved),
        repository_realpath=repository_realpath,
        head_sha=head,
        branch=None if branch in {None, "", "HEAD"} else branch,
        dirty=_read_dirty(str(resolved)),
    )


def build_source_binding(
    *,
    repository: str,
    worktree_path: Any,
    base_repository_path: Any,
    task_id: str,
    work_id: str,
    dirty_expectation: DirtyStateExpectation = DirtyStateExpectation.CLEAN_REQUIRED,
) -> RepositorySourceBinding:
    """Mint a binding from an *observed* source, guarded by a configured base.

    ``base_repository_path`` must be the configured canonical root for
    ``repository``. The candidate ``worktree_path`` is only bindable when git
    reports it as that base root or as a registered worktree of it. A
    caller-supplied host path therefore cannot become authoritative on its own:
    an arbitrary sibling directory, clone or mirror is rejected.
    """

    observed = observe_source_workspace(worktree_path)
    if not observed.available:
        raise ValueError(
            f"cannot bind unavailable source: {observed.unavailable_reason}"
        )
    base = observe_source_workspace(base_repository_path)
    if not base.available:
        raise ValueError(
            f"configured repository root is unusable: {base.unavailable_reason}"
        )
    if not _same_path(base.repository_realpath, observed.repository_realpath):
        raise ValueError(
            "requested worktree does not belong to the configured repository root"
        )
    registered = _registered_worktrees(base.worktree_realpath)
    if observed.worktree_realpath not in registered:
        raise ValueError(
            "requested worktree is not a registered worktree of the repository"
        )
    # Preserve required provenance; never synthesize an identity or
    # silently downgrade a malformed bound task to unbound.
    return RepositorySourceBinding(
        repository=repository,
        repo_realpath=str(base.worktree_realpath),
        worktree_realpath=str(observed.worktree_realpath),
        branch=observed.branch or "DETACHED",
        head_sha=str(observed.head_sha),
        expected_dirty=dirty_expectation,
        task_id=task_id,
        work_id=work_id,
    )


def _reject(
    state: SourceBindingState,
    binding: RepositorySourceBinding,
    observed: ObservedSource,
    reason: str,
) -> SourceBindingVerification:
    return SourceBindingVerification(
        state=state,
        accepted=False,
        reasons=[reason],
        repository=binding.repository,
        expected=binding,
        observed=observed,
    )


def _same_path(observed: str | None, expected: str | None) -> bool:
    if not observed or not expected:
        return False
    try:
        return os.path.realpath(observed) == os.path.realpath(expected)
    except OSError:  # pragma: no cover - defensive
        return False


def _same_head(observed: str | None, expected: str) -> bool:
    if not observed:
        return False
    return observed.strip().lower() == expected.strip().lower()


def _read_dirty(worktree_realpath: str | None) -> bool | None:
    """True when the worktree has changes, False when clean, None when unreadable."""

    if not worktree_realpath:
        return None
    ok, stdout = _git_capture(["status", "--porcelain"], worktree_realpath)
    if not ok:
        return None
    return bool(stdout.strip())


def _repository_root(resolved: Path, common_dir: str | None) -> str | None:
    """Canonical main-checkout path that owns the shared object database."""

    if common_dir:
        common = Path(common_dir)
        # For a linked worktree ``--git-common-dir`` is the bare repository's
        # ``.git`` directory; for the main checkout it is ``<root>/.git``.
        if common.name == ".git":
            return str(common.parent.resolve())
        if (common / ".git").exists():
            return str((common / ".git").parent.resolve())
        return str(common.parent.resolve())
    if (resolved / ".git").is_dir():
        return str(resolved.resolve())
    return None


def _registered_worktrees(repository_root: str) -> set[str]:
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repository_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    paths: set[str] = set()
    for line in (result.stdout or "").splitlines():
        if line.startswith("worktree "):
            try:
                paths.add(os.path.realpath(line[len("worktree ") :].strip()))
            except OSError:  # pragma: no cover - defensive
                continue
    return paths


def _git_capture(args: list[str], cwd: str) -> tuple[bool, str]:
    """Run git and return (succeeded, stdout). Empty stdout is valid output."""

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return result.returncode == 0, result.stdout or ""


def _git_text(args: list[str], cwd: str) -> str | None:
    ok, stdout = _git_capture(args, cwd)
    return stdout.strip() if ok else None
