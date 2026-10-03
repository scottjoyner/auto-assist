"""Repository source-binding contract.

Answers exactly one question: *which source was (or must be) under examination?*

A repository-bound task or delegation carries a ``RepositorySourceBinding`` so a
worker cannot silently satisfy it from a stale mirror. The binding is
**identity only** -- it grants no claim, execution, write, dispatch, approval or
routing authority. It exists so that an observed workspace can be compared
against an expected source and *fails closed* on any difference.

Why stale mirrors are forbidden
-------------------------------
The fleet historically accepted "some checkout of the right repository". That
is not enough: a reviewer that inspects a stale mirror produces confident,
well-formed findings about code that is not the requested code, and nothing in
the result distinguishes that from a real review. Binding the *worktree
realpath* together with the HEAD commit collapses that ambiguity -- there is
exactly one accepted source and everything else is a mismatch, never a fallback.
"""

from __future__ import annotations

import os
import posixpath
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

# A repository identity is an opaque alias (``auto-assist``), never a path.
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = ("\x00", "\n", "\r")
_BAD_REF_CHARS = (" ", "~", "^", ":", "?", "*", "[", "\\")


class DirtyStateExpectation(str, Enum):
    """What the bound source is allowed to look like before work begins."""

    CLEAN_REQUIRED = "clean_required"
    DIRTY_ALLOWED = "dirty_allowed"


class SourceBindingState(str, Enum):
    """Explicit verification outcome. Anything other than MATCH is a rejection."""

    MATCH = "MATCH"
    REPOSITORY_MISMATCH = "REPOSITORY_MISMATCH"
    WORKTREE_MISMATCH = "WORKTREE_MISMATCH"
    BRANCH_MISMATCH = "BRANCH_MISMATCH"
    HEAD_MISMATCH = "HEAD_MISMATCH"
    DIRTY_STATE_MISMATCH = "DIRTY_STATE_MISMATCH"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    UNBOUND = "UNBOUND"


def _reject_home(candidate: str, field_name: str) -> None:
    """Refuse the home directory as a bound source.

    ``$HOME`` is the classic silent fallback when a caller cannot resolve the
    requested worktree, so it must never be an accepted binding.
    """

    try:
        home = os.path.expanduser("~")
    except Exception:  # pragma: no cover - defensive
        return
    if not home or home == "~" or not os.path.isabs(home):
        return
    if os.path.realpath(candidate) == os.path.realpath(home):
        raise ValueError(f"{field_name} must not be the home directory")


def _validate_realpath(value: str, field_name: str) -> str:
    raw = str(value)
    if not raw:
        raise ValueError(f"{field_name} is required")
    if raw != raw.strip():
        raise ValueError(f"{field_name} must not be padded with whitespace")
    if any(mark in raw for mark in _CONTROL):
        raise ValueError(f"{field_name} contains control characters")
    if raw != posixpath.normpath(raw):
        raise ValueError(f"{field_name} must be normalized")
    if not posixpath.isabs(raw):
        raise ValueError(f"{field_name} must be an absolute path")
    if any(part == ".." for part in raw.split("/")):
        raise ValueError(f"{field_name} must not contain '..'")
    if len([part for part in raw.split("/") if part]) < 2:
        raise ValueError(f"{field_name} must not be a filesystem root")
    _reject_home(raw, field_name)
    return raw


class RepositorySourceBinding(BaseModel):
    """Fail-closed description of the one accepted source for a repository task.

    Validation is deliberately strict: a caller-supplied host path is never
    authoritative on its own, so every path must already be a canonical
    absolute realpath and cannot be relative, traversable, the home directory,
    or a filesystem root.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str = Field(
        ..., description="Repository identity/alias (opaque, never a path)."
    )
    repository_realpath: str = Field(
        ..., description="Canonical realpath of the repository (main) checkout."
    )
    worktree_realpath: str = Field(
        ..., description="Canonical realpath of the exact worktree under work."
    )
    branch: str | None = Field(
        None, description="Expected branch ref name; None means branch-agnostic."
    )
    head_sha: str = Field(..., description="Expected full HEAD commit SHA.")
    dirty_expectation: DirtyStateExpectation = Field(
        default=DirtyStateExpectation.CLEAN_REQUIRED,
        description="Whether the bound worktree must be clean.",
    )
    task_id: str | None = Field(
        None, description="Task id this binding was minted for."
    )
    work_id: str | None = Field(
        None, description="Delegation/work identifier this binding was minted for."
    )
    source_manifest_sha256: str | None = Field(
        None,
        description="Optional digest of a source manifest covering the bound tree.",
    )

    @field_validator("repository")
    @classmethod
    def _validate_identity(cls, value: str) -> str:
        identity = str(value).strip()
        if not _IDENTITY_RE.match(identity):
            raise ValueError(
                "repository identity must be an opaque alias "
                "(letters, digits, dot, dash, underscore); paths are not allowed"
            )
        return identity

    @field_validator("repository_realpath", "worktree_realpath")
    @classmethod
    def _validate_paths(cls, value: str, info: ValidationInfo) -> str:
        return _validate_realpath(value, info.field_name)

    @field_validator("head_sha")
    @classmethod
    def _validate_head(cls, value: str) -> str:
        head = str(value).strip().lower()
        if not (_SHA1_RE.match(head) or _SHA256_RE.match(head)):
            raise ValueError("head_sha must be a full lowercase hexadecimal commit SHA")
        return head

    @field_validator("branch")
    @classmethod
    def _validate_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        branch = str(value).strip()
        if not branch or branch in {"HEAD", "detached"}:
            return None
        if any(mark in branch for mark in (*_CONTROL, *_BAD_REF_CHARS)):
            raise ValueError("branch contains characters that are not allowed in a ref")
        return branch

    @field_validator("source_manifest_sha256")
    @classmethod
    def _validate_manifest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digest = str(value).strip().lower()
        if not _SHA256_RE.match(digest):
            raise ValueError("source_manifest_sha256 must be a sha-256 hex digest")
        return digest

    @field_validator("task_id", "work_id")
    @classmethod
    def _validate_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        identifier = str(value).strip()
        if not identifier:
            return None
        if len(identifier) > 256 or any(mark in identifier for mark in _CONTROL):
            raise ValueError("identifier must be short and free of control characters")
        return identifier

    def to_contract_payload(self) -> dict[str, Any]:
        """Serialize for embedding in a task or delegation payload."""

        return self.model_dump(mode="json", exclude_none=True)

    @classmethod
    def from_contract_payload(cls, payload: Any) -> RepositorySourceBinding | None:
        """Parse a binding out of an untrusted payload, failing closed.

        Returns ``None`` when no binding is present at all (legacy or
        non-repository tasks). Raises when a binding *is* present but invalid,
        because a malformed binding must never be silently downgraded to
        "unbound" and thereby skip verification.
        """

        if payload is None:
            return None
        if isinstance(payload, RepositorySourceBinding):
            return payload
        if not isinstance(payload, dict):
            raise ValueError("source binding must be an object")
        if not payload:
            return None
        return cls.model_validate(payload)


__all__ = [
    "DirtyStateExpectation",
    "RepositorySourceBinding",
    "SourceBindingState",
]
