# Repository Source Binding Contract

_Last updated: 2026-10-02_

## Core decision

A repository-bound task or delegation records **which checkout it is about**, and
that source is verified against the workspace actually used **before** the work is
accepted.

A source binding is a *description*. It grants no task claim, no execution, no
dispatch, no approval, and no routing authority. Nothing in this contract may be
used to widen an execution-authority fence. See `EXECUTION_AUTHORITY.md`.

## Why stale mirrors are forbidden

A Hermes swarm reviewer was handed a repository-bound review and silently
inspected the wrong checkout:

```text
observed:  /home/scott/embed_x1
expected:  the authoritative dedicated worktree
```

The review evaluated stale source, produced a confident and internally consistent
verdict, and had to be rejected.

The dangerous property is not that it read the wrong directory. It is that **a
stale mirror produces a plausible answer**. Every correctness signal downstream —
a coherent review, a passing test summary, a confident recommendation — is
perfectly consistent with source that is months out of date. Nothing in the result
distinguished "correct" from "correct about the wrong thing". So source identity
has to be pinned *before* work starts and re-checked at use time. A wrong checkout
is a contract failure, not a nuisance.

This host is unusually prone to that error: the same repository exists as a
canonical clone, symlinked clones, detached worktrees, dated reconciliation
branches, review copies, NAS mirrors, dated knowledge-backups, and a Tailscale
mirror on another node. They share a project name. They differ in content.

## What a binding records

`src/assistx/contracts/schemas/repository_source_binding.py` —
`RepositorySourceBinding`, `schema_version = assistx-repository-source-binding-v1`.

| Field | Meaning |
|---|---|
| `repository` | Registry key for the project (never a filesystem path) |
| `repository_realpath` | Canonical realpath of the repository's **primary** checkout |
| `worktree_realpath` | Canonical realpath of the **worktree the work runs in** |
| `branch` | Branch name, or `DETACHED` when pinned to a commit |
| `head_sha` | Expected 40-char lowercase HEAD |
| `expect_dirty` | `clean` (default), `dirty`, or `any` |
| `work_id` | Task / delegation / work identifier |
| `source_manifest_sha256` | Optional digest proving which files were in scope |

`repository_realpath` and `worktree_realpath` are deliberately distinct. A
worktree belongs to a repository but is not the repository's primary checkout, and
"right repository, wrong worktree" is a real failure mode.

All path fields must already be canonical (`os.path.realpath` output): absolute,
normalized, no `..`, no trailing separator. The validator does **not** resolve
paths itself. Resolving inside the contract would hide a symlinked or relocated
checkout behind a path that looks trustworthy.

## Trust boundary: a supplied path is an assertion, not authority

A caller-supplied host path never becomes authoritative by appearing in a binding.
The only trusted anchor is the operator-configured `ASSISTX_REPOSITORY_ROOTS_JSON`
registry.

`bind_repository_source(repository, worktree, ...)`
(`src/assistx/repository_source_binding.py`):

1. refuses any repository absent from the registry → `repository_root_not_configured`;
2. observes `worktree` empirically with `git`;
3. refuses if the observed repository's common git dir is not the registered root
   → `registered_repository_root_mismatch`;
4. only then mints a binding.

Identity is derived from `git` running **inside** the directory, never from the
caller's label for it.

## Verification states

`verify_source_binding(expected, observed) -> SourceBindingVerification`

| State | Meaning |
|---|---|
| `MATCH` | Repository realpath, worktree realpath, HEAD and dirty expectation all agree |
| `WORKTREE_MISMATCH` | Different worktree than the one pinned |
| `HEAD_MISMATCH` | Right worktree, wrong commit |
| `REPOSITORY_MISMATCH` | Wrong repository entirely (e.g. a same-named mirror) |
| `SOURCE_UNAVAILABLE` | Pinned source is missing, unreadable, or not a git checkout |
| `DIRTY_STATE_MISMATCH` | Identity agrees; dirty state disagrees with the expectation |

Precedence is fixed so one cause is always reported first:

```text
SOURCE_UNAVAILABLE > REPOSITORY_MISMATCH > WORKTREE_MISMATCH > HEAD_MISMATCH > DIRTY_STATE_MISMATCH
```

Dirty state is judged only after identity agrees, because "you examined a
different directory" is more actionable than "there are uncommitted changes".

## No fallback, ever

On any mismatch the verifier returns a rejection. It does not:

* resolve a different checkout of the same repository;
* fall back to `$HOME`;
* search by repository name;
* accept `/home/scott/embed_x1` or any other stale mirror;
* substitute another worktree belonging to the same repository.

A fallback is the defect, not the remedy. When the pinned source is unavailable the
correct answer is `SOURCE_UNAVAILABLE` and the caller decides what to do.

`SourceBindingVerification.fallback_attempted` is always `False` and exists so tests
can pin that invariant.

`grants_authority` is always `False` and exists for the same reason.

## Provenance

`build_execution_contract(..., source_binding=<binding>)` records the binding under
`execution_contract.source_binding`. `task_source_binding(task)` reads it back.

This is **optional and additive**. Tasks without a `source_binding` are unaffected,
which is what keeps non-repository tasks working unchanged. Recording the binding
lets a later reviewer prove which source it examined — the thing that was missing
when the original review had to be thrown out.

## Tests

`tests/test_repository_source_binding.py` covers:

| Case | Expected |
|---|---|
| Exact requested worktree + HEAD | `MATCH` |
| Same repository, different worktree | `WORKTREE_MISMATCH` |
| Same worktree, stale HEAD | `HEAD_MISMATCH` |
| Different mirror, same repository name | `REPOSITORY_MISMATCH` |
| Missing worktree | `SOURCE_UNAVAILABLE` |
| Non-repository task | unaffected, no `source_binding` |

Plus: an unregistered repository is refused; a caller path outside the registry is
refused; a mirror at an identical HEAD is still rejected; a same-named checkout
under `$HOME` is not adopted; a clean-expectation violation is reported as
`DIRTY_STATE_MISMATCH`; and malformed or unknown contract fields raise.

## Scope

This contract is a source-integrity fix. It does not change execution authority,
scheduling, or dispatch.