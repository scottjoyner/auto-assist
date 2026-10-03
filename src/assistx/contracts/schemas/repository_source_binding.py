"""Repository source binding contract (which checkout a task/delegation is about).

A source binding is a *description* of repository source identity, not a grant.
It confers no task claim, no execution, no dispatch, no approval, and no routing
authority. Nothing in this module reads or writes runtime state.

Why this exists
---------------
A reviewer once evaluated ``/home/scott/embed_x1`` instead of the authoritative
worktree, produced a verdict from stale source, and had to be rejected. The
failure was invisible because nothing recorded *which* checkout was examined.

A stale mirror is worse than an error: it produces a confident, plausible,
wrong answer. So source identity must be pinned *before* work starts and
*re-checked* against the workspace actually used.

Trust boundary
--------------
Paths in this contract are **assertions, not authority**. A caller-supplied host
path never becomes authoritative by appearing here. The only trusted anchor is
the operator-configured ``ASSISTX_REPOSITORY_ROOTS_JSON`` registry; see
:func:`assistx.repository_source_binding.bind_repository_source`, which refuses
to mint a binding for any repository absent from that registry.

All path fields must already be canonical (``os.path.realpath`` output):
absolute, normalized, with no ``..`` segment, no trailing separator, and no
NUL/control characters. Accepting a non-canonical value would let two spellings
of the same directory compare unequal, or one caller pass ``$HOME`` and have it
silently treated as the worktree.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SOURCE_BINDING_SCHEMA = "assistx-repository-source-binding-v1"

_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

MAX_ID_LENGTH = 200
MAX_PATH_LENGTH = 4096


class DirtyStateExpectation(str, Enum):
    """What the task author expects the bound worktree's dirty state to be."""

    CLEAN = "clean"
    DIRTY = "dirty"
    ANY = "any"


def _canonical_absolute_path(value: Any, label: str) -> str:
    """Require an already-canonical absolute realpath.

    Deliberately does *not* call ``os.path.realpath``. Canonicalization is the
    caller's job at observation time; silently resolving here would hide a
    symlinked or relocated checkout behind a path that looks trustworthy.
    """
    text = str(value or "")
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > MAX_PATH_LENGTH:
        raise ValueError(f"{label} exceeds {MAX_PATH_LENGTH} characters")
    if _CONTROL.search(text):
        raise ValueError(f"{label} contains control characters")
    if not text.startswith("/"):
        raise ValueError(f"{label} must be an absolute path: {text!r}")
    if text.endswith("/") and text != "/":
        raise ValueError(f"{label} must not end with a path separator")
    parts = text.split("/")
    if any(part == ".." for part in parts):
        raise ValueError(f"{label} must be a normalized realpath without '..'")
    if "//" in text:
        raise ValueError(f"{label} must not contain empty path segments")
    return text


def _identifier(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > MAX_ID_LENGTH:
        raise ValueError(f"{label} exceeds {MAX_ID_LENGTH} characters")
    if _CONTROL.search(text):
        raise ValueError(f"{label} contains control characters")
    return text


class RepositorySourceBinding(BaseModel):
    """Pinned repository source identity for one task or delegation.

    Populate every field from a single observation of the workspace the work
    will actually run in, then verify the workspace again at use time with
    :func:`assistx.repository_source_binding.verify_source_binding`.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SOURCE_BINDING_SCHEMA)
    repository: str = Field(
        ..., description="Repository identity/alias as keyed in the repository registry."
    )
    repository_realpath: str = Field(
        ..., description="Canonical realpath of the repository's primary checkout."
    )
    worktree_realpath: str = Field(
        ..., description="Canonical realpath of the worktree the work runs in."
    )
    branch: str = Field(..., description="Branch name, or 'DETACHED' at a pinned commit.")
    head_sha: str = Field(..., description="Expected 40-character lowercase HEAD SHA.")
    expect_dirty: DirtyStateExpectation = Field(
        default=DirtyStateExpectation.CLEAN,
        description="Expected dirty state of the bound worktree.",
    )
    work_id: str = Field(..., description="Task, delegation, or work identifier.")
    source_manifest_sha256: str | None = Field(
        default=None,
        description=(
            "Optional SHA-256 of a source manifest, proving which files were "
            "in scope. Presence is optional; when supplied it must be a digest."
        ),
    )

    @field_validator("repository")
    @classmethod
    def _valid_repository(cls, value: str) -> str:
        text = _identifier(value, "repository")
        if "/" in text or "\\" in text or text in {".", ".."}:
            raise ValueError(
                "repository must be a registry key, not a filesystem path: "
                f"{value!r}"
            )
        return text

    @field_validator("repository_realpath")
    @classmethod
    def _valid_repository_realpath(cls, value: str) -> str:
        return _canonical_absolute_path(value, "repository_realpath")

    @field_validator("worktree_realpath")
    @classmethod
    def _valid_worktree_realpath(cls, value: str) -> str:
        return _canonical_absolute_path(value, "worktree_realpath")

    @field_validator("branch")
    @classmethod
    def _valid_branch(cls, value: str) -> str:
        text = _identifier(value, "branch")
        if text.startswith("-"):
            raise ValueError("branch must not start with '-': it would be read as a git flag")
        return text

    @field_validator("head_sha")
    @classmethod
    def _valid_head_sha(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _HEAD_SHA.fullmatch(text):
            raise ValueError("head_sha must be a 40-character lowercase git SHA")
        return text

    @field_validator("work_id")
    @classmethod
    def _valid_work_id(cls, value: str) -> str:
        return _identifier(value, "work_id")

    @field_validator("source_manifest_sha256")
    @classmethod
    def _valid_manifest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text.startswith("sha256:"):
            text = text.removeprefix("sha256:")
        if not _SHA256.fullmatch(text):
            raise ValueError("source_manifest_sha256 must be a 64-character SHA-256 digest")
        return text
