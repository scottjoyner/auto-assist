"""Repository source-binding contract (which tree a task must examine).

A repository-bound task is only meaningful if the executor, reviewer, or
delegate that ends up reading the source reads the *same tree* the task was
created against. A recent swarm reviewer inspected ``/home/scott/embed_x1``
instead of the authoritative worktree
``/media/scott/SSD_4TB/worktrees/auto-ingest-swarm-20261002``; its review
evaluated stale source and had to be rejected. Nothing in the task payload
recorded that the wrong checkout had been used, so the error was only visible
after the fact.

``RepositorySourceBinding`` closes that gap. It carries exactly the identity a
worker must be able to prove it examined.

Design constraints, all deliberate:

* The binding records *identity*, never *authority*. Constructing, validating,
  or verifying one grants no task claim, no execution, no write, no dispatch,
  no approval, and no routing authority. ``SourceBindingVerdict`` pins every
  authority flag to ``False`` so the boundary is machine-checkable.
* Caller-supplied host paths are not authoritative on their own. The binding
  must declare ``authority_source``, meaning both realpaths were resolved
  through the operator-controlled repository alias map rather than taken on
  trust from whoever called.
* Paths must be canonical realpaths. Relative paths, ``~`` prefixes, ``..``
  traversal, and the home directory itself are rejected at validation time.
* Stale mirrors are forbidden. Two checkouts of the same repository are
  different sources even when they share a remote, a branch, and a commit, so
  the worktree realpath is pinned separately from the repository realpath.

This module's own imports are limited to ``pydantic`` and the standard library.
**Importing it through the ``assistx`` package is not side-effect free, however.**
``assistx/__init__.py`` executes five runtime safety-boundary installers at import
time - claim fencing, task-family routing, repository path policy, work-supply
boundaries, and fleet-executor compatibility - so a plain
``from assistx.contracts.schemas.repository_source_binding import ...`` loads the
Neo4j driver, FastAPI, httpx, and pandas and mutates ``Neo4jClient`` methods as a
side effect. That is measured, not theoretical: roughly 1200 modules.

A consumer that only needs this contract, and must not pull in AssistX runtime
state, should load the file directly instead:

.. code-block:: python

    import importlib.util, pathlib, sys

    path = pathlib.Path("src/assistx/contracts/schemas/repository_source_binding.py")
    spec = importlib.util.spec_from_file_location("repository_source_binding", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # pydantic resolves annotations via sys.modules
    spec.loader.exec_module(module)

That path loads pydantic and the standard library only. The ``sys.modules``
registration is required: with ``from __future__ import annotations`` pydantic
resolves the string annotations through ``sys.modules[cls.__module__]`` and
otherwise reports the model as not fully defined. See
``tests/test_repository_source_binding.py`` for a pinned test of both properties.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HEAD_SHA_PATTERN = r"^[0-9a-f]{40}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: The only accepted provenance for a binding's two realpaths. Anything else
#: would let a caller nominate an arbitrary host path as authoritative.
BINDING_AUTHORITY_SOURCE = "configured_repository_roots"


def _validate_canonical_path(value: str, *, field: str) -> str:
    """Reject anything that is not an absolute, canonical realpath."""

    text = str(value or "")
    if not text.strip():
        raise ValueError(f"{field} must not be empty")
    if _CONTROL.search(text):
        raise ValueError(f"{field} must not contain control characters")
    if text.startswith("~"):
        raise ValueError(f"{field} must be a resolved realpath, not a ~ path")
    if not text.startswith("/"):
        raise ValueError(f"{field} must be an absolute path, not {text!r}")
    if ".." in PurePosixPath(text).parts:
        raise ValueError(f"{field} must not contain '..' traversal segments")
    if text != os.path.normpath(text):
        raise ValueError(f"{field} must be canonical, not {text!r}")
    try:
        home = os.path.normpath(os.path.realpath(os.path.expanduser("~")))
    except (OSError, RuntimeError):
        home = ""
    # $HOME is never a legitimate repository or worktree root. Treating it as
    # one is the specific silent-fallback failure this contract exists to stop.
    if home and os.path.normpath(text) == home:
        raise ValueError(f"{field} must not be the home directory")
    return text


class DirtyStateExpectation(str, Enum):
    """What the bound worktree's dirty state must be for the binding to hold.

    ``ANY`` exists for read-only analysis of an already-dirty operator
    checkout. It relaxes cleanliness only; it never relaxes path or HEAD
    identity.
    """

    CLEAN = "clean"
    CLEAN_REQUIRED = "clean_required"
    DIRTY = "dirty"
    ANY = "any"


class SourceBindingState(str, Enum):
    """Outcome of comparing an observed workspace against an expected binding.

    Every value other than ``MATCH`` is a fail-closed rejection. There is no
    "close enough" and no "try another checkout" state, by construction.
    """

    MATCH = "MATCH"
    REPOSITORY_MISMATCH = "REPOSITORY_MISMATCH"
    WORKTREE_MISMATCH = "WORKTREE_MISMATCH"
    HEAD_MISMATCH = "HEAD_MISMATCH"
    DIRTY_STATE_MISMATCH = "DIRTY_STATE_MISMATCH"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class RepositorySourceBinding(BaseModel):
    """The tree a repository-bound task is expected to read.

    This is an integrity record, not a capability. Holding one proves nothing
    about who may execute. See ``docs/swarm_contracts/repository_source_binding.md``.
    """

    model_config = ConfigDict(extra="forbid")

    authority_source: str = Field(
        default=BINDING_AUTHORITY_SOURCE,
        description=(
            "Declares how the two realpaths were resolved. Pinned so a "
            "caller-supplied path is never authoritative on its own."
        ),
    )

    repository: str = Field(..., description="Configured repository identity alias.")
    repo_realpath: str = Field(
        ...,
        description="Canonical realpath of the repository the worktree belongs to.",
    )
    worktree_realpath: str = Field(
        ...,
        description="Canonical realpath of the exact worktree to be examined.",
    )
    branch: str = Field(..., min_length=1, max_length=512)
    head_sha: str = Field(..., pattern=HEAD_SHA_PATTERN)
    expected_dirty: DirtyStateExpectation = DirtyStateExpectation.CLEAN
    task_id: str = Field(..., min_length=1, max_length=256)
    work_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Attempt/execution identifier the binding is scoped to.",
    )
    source_manifest_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @field_validator("authority_source")
    @classmethod
    def _pinned_authority_source(cls, value: str) -> str:
        if value != BINDING_AUTHORITY_SOURCE:
            raise ValueError(
                f"authority_source must be {BINDING_AUTHORITY_SOURCE!r}; a "
                "caller-supplied path is never authoritative on its own"
            )
        return value

    @field_validator("repository")
    @classmethod
    def _identity_is_not_a_path(cls, value: str) -> str:
        text = str(value or "")
        if not text.strip():
            raise ValueError("repository must not be empty")
        if _CONTROL.search(text):
            raise ValueError("repository must not contain control characters")
        if text.startswith(("/", "~")):
            raise ValueError(
                "repository must be a configured identity alias, not a host path"
            )
        if ".." in PurePosixPath(text).parts:
            raise ValueError("repository must not contain '..' segments")
        return text

    @field_validator("repo_realpath", "worktree_realpath")
    @classmethod
    def _realpath_is_canonical(cls, value: str, info) -> str:
        return _validate_canonical_path(value, field=info.field_name)

    @field_validator("task_id", "work_id")
    @classmethod
    def _identifier_is_clean(cls, value: str) -> str:
        if _CONTROL.search(str(value or "")):
            raise ValueError("task_id/work_id must not contain control characters")
        return value

    @classmethod
    def from_contract_payload(
        cls, payload: RepositorySourceBinding | dict[str, object] | None
    ) -> RepositorySourceBinding | None:
        """Parse optional contracts without converting malformed bindings to unbound.

        This restores the call-site API after the stricter schema migration.
        An omitted binding is supported for legacy tasks; an explicitly empty
        or malformed binding is never silently accepted as an unbound task.
        """
        if payload is None:
            return None
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, dict) or not payload:
            raise ValueError("invalid repository source binding")
        return cls.model_validate(payload)

    def to_contract_payload(self) -> dict[str, object]:
        """Canonical JSON-ready binding; never drop authority or path fields."""
        return self.provenance()

    def provenance(self) -> dict[str, object]:
        """Compact payload safe to embed in a task or result."""

        return self.model_dump(mode="json")


class ObservedSourceState(BaseModel):
    """What one specific executor/reviewer workspace actually looks like.

    Produced by observing exactly one named directory. There is deliberately no
    constructor that accepts a *search root*: a verifier must be told which
    workspace to inspect, and if that workspace is not a usable git checkout
    the answer is ``SOURCE_UNAVAILABLE`` rather than a different directory.
    """

    model_config = ConfigDict(extra="forbid")

    available: bool = True
    unavailable_reason: str | None = Field(default=None, max_length=256)
    repository: str | None = Field(default=None, max_length=512)
    repo_realpath: str | None = Field(default=None, max_length=4096)
    worktree_realpath: str | None = Field(default=None, max_length=4096)
    branch: str | None = Field(default=None, max_length=512)
    head_sha: str | None = Field(default=None, max_length=64)
    dirty: bool | None = None

    @classmethod
    def unavailable(cls, reason: str) -> ObservedSourceState:
        return cls(available=False, unavailable_reason=str(reason)[:256])


class SourceBindingVerdict(BaseModel):
    """Fail-closed comparison of an observed workspace against a binding."""

    model_config = ConfigDict(extra="forbid")

    state: SourceBindingState
    task_id: str
    work_id: str
    repository: str

    expected_repo_realpath: str
    expected_worktree_realpath: str
    expected_branch: str
    expected_head_sha: str

    observed_repo_realpath: str | None = None
    observed_worktree_realpath: str | None = None
    observed_branch: str | None = None
    observed_head_sha: str | None = None
    observed_dirty: bool | None = None

    reasons: list[str] = Field(default_factory=list)

    #: Always empty. It is recorded so a reader can confirm no fallback was
    #: taken; the implementation has no code path that could populate it.
    fallback_candidates_considered: list[str] = Field(default_factory=list)

    #: A source binding is an integrity check. It grants nothing.
    dispatch_allowed: Literal[False] = False
    approval_granted: Literal[False] = False
    claim_acquired: Literal[False] = False
    mutation_allowed: Literal[False] = False
    execution_authority_granted: Literal[False] = False
    routing_authority_changed: Literal[False] = False

    @property
    def accepted(self) -> bool:
        return self.state is SourceBindingState.MATCH

    def provenance(self) -> dict[str, object]:
        """Proof of which source was examined, embeddable in a task result.

        A later reviewer can use this to tell whether an analysis was actually
        performed against the tree it claimed to analyse.
        """

        return {
            "state": self.state.value,
            "repository": self.repository,
            "task_id": self.task_id,
            "work_id": self.work_id,
            "expected_worktree_realpath": self.expected_worktree_realpath,
            "expected_head_sha": self.expected_head_sha,
            "observed_worktree_realpath": self.observed_worktree_realpath,
            "observed_head_sha": self.observed_head_sha,
            "reasons": list(self.reasons),
            "fallback_candidates_considered": list(self.fallback_candidates_considered),
        }


__all__ = [
    "BINDING_AUTHORITY_SOURCE",
    "HEAD_SHA_PATTERN",
    "SHA256_PATTERN",
    "DirtyStateExpectation",
    "ObservedSourceState",
    "RepositorySourceBinding",
    "SourceBindingState",
    "SourceBindingVerdict",
]
