# Repository Source Binding Contract

_Last updated: 2026-10-02_

## Purpose

A repository-bound task is only meaningful if the worker that ends up reading
the source reads the *same tree* the task was created against.

A recent Hermes swarm reviewer inspected:

```text
/home/scott/embed_x1
```

instead of the authoritative dedicated worktree:

```text
/media/scott/SSD_4TB/worktrees/auto-ingest-swarm-20261002
```

Its review therefore evaluated stale source and had to be rejected. Nothing in
the task payload recorded *which tree was examined*, so the error was only
discoverable after the work was done — and the review itself was
indistinguishable from a correct one until a human checked.

This contract makes that class of error fail closed at the task/delegation
boundary instead of at review time.

---

## Core rule

**A binding records identity, never authority.**

A `RepositorySourceBinding` proves where source came from. It grants no task
claim, no execution, no write, no dispatch, no approval, and no routing
authority. `SourceBindingVerdict` pins all six of those flags to `False` with
`Literal[False]`, so the boundary is machine-checkable rather than a code
review convention.

## Why stale mirrors are forbidden

Two checkouts of the same repository are *different sources*, even when they
share a remote, a branch, and a commit:

| Checkout | What it actually is |
|---|---|
| `/media/scott/SSD_4TB/worktrees/auto-ingest-swarm-20261002` | the authoritative worktree for the campaign |
| `/home/scott/embed_x1` | an unrelated local mirror, months behind |
| `<repo>/../other-worktree` | a sibling worktree at a different revision |

Accepting "some checkout of the right repository" is what produced the incident.
A stale mirror does not fail loudly; it returns plausible, well-formed findings
about code that is not the code that was asked about. The only defence is to pin
the *exact* tree and reject everything else.

---

## Schema

```yaml
authority_source: configured_repository_roots   # pinned, see below
repository: string          # configured identity alias, never a path
repo_realpath: string       # canonical realpath of the owning repository
worktree_realpath: string   # canonical realpath of the exact worktree
branch: string              # "DETACHED" when the binding is commit-only
head_sha: string            # ^[0-9a-f]{40}$
expected_dirty: clean | dirty | any
task_id: string
work_id: string             # attempt/execution identifier
source_manifest_sha256: optional string   # ^[0-9a-f]{64}$
```

### `repo_realpath` vs `worktree_realpath`

Both are pinned, and they are not redundant. `repo_realpath` is derived from
`git rev-parse --git-common-dir`, which follows a worktree link back to the
shared repository. That is what lets the verifier distinguish *same repository,
different worktree* (`WORKTREE_MISMATCH`) from *different repository entirely*
(`REPOSITORY_MISMATCH`).

### `authority_source` is pinned

`authority_source` may only be `configured_repository_roots`. This exists so
that a caller-supplied host path is never authoritative on its own: both
realpaths must have been resolved through the operator-controlled alias map

```text
ASSISTX_REPOSITORY_ROOTS_JSON={"auto-ingest":"..."}
```

which `build_binding()` enforces. An alias absent from that map has no
authority.

### Path validation is fail-closed

`RepositorySourceBinding` rejects, at validation time:

- relative paths;
- `~` prefixes (unresolved);
- `..` traversal segments;
- non-canonical paths (`/a//b`);
- **the home directory** — `$HOME` is never a legitimate repository or
  worktree root, and treating it as one is the specific silent-fallback failure
  this contract exists to stop;
- a host path smuggled in as the `repository` identity.

---

## Verification states

| State | Meaning |
|---|---|
| `MATCH` | the observed workspace is the bound tree at the bound HEAD |
| `REPOSITORY_MISMATCH` | a different repository (e.g. the stale mirror) |
| `WORKTREE_MISMATCH` | right repository, wrong worktree |
| `HEAD_MISMATCH` | right tree, stale or moved revision |
| `DIRTY_STATE_MISMATCH` | right tree and revision, wrong cleanliness |
| `SOURCE_UNAVAILABLE` | the named workspace is missing or unreadable |

Checks run most-fundamental identity first, so the reported state names the real
problem rather than a downstream symptom. Every non-`MATCH` value is a
rejection. There is no partial acceptance.

## What the verifier will never do

`observe_source(path)` reads exactly the path it is given. If it is missing,
unreadable, or not a git checkout, the answer is `SOURCE_UNAVAILABLE`. It does
not:

- search for another checkout of the same repository name;
- fall back to `$HOME`;
- accept a sibling worktree of the same repository;
- substitute `/home/scott/embed_x1` or any other path.

`SourceBindingVerdict.fallback_candidates_considered` is recorded as always
empty so a reader can confirm no fallback was taken. Tests assert it.

Note that `repo_task_generator._get_repo_info` previously collapsed a failed
`git status` into "clean" (`bool(_git_text(...) or "")`). `observe_source`
distinguishes a non-zero return from empty output and returns
`SOURCE_UNAVAILABLE` instead of guessing.

---

## Provenance

Repository-bound task payloads now carry `source_binding`, so a reviewer result
can later prove what it examined. `SourceBindingVerdict.provenance()` returns a
compact block suitable for embedding in a task result:

```json
{
  "state": "MATCH",
  "repository": "auto-ingest-swarm",
  "task_id": "repo-analysis-…",
  "work_id": "repo-analysis-…",
  "expected_worktree_realpath": "/media/scott/SSD_4TB/worktrees/auto-ingest-swarm-20261002",
  "expected_head_sha": "…",
  "observed_worktree_realpath": "/media/scott/SSD_4TB/worktrees/auto-ingest-swarm-20261002",
  "observed_head_sha": "…",
  "reasons": [],
  "fallback_candidates_considered": []
}
```

`binding_from_payload()` returns `None` for tasks with no binding, which keeps
non-repository tasks entirely unaffected. It **raises** when a binding is
present but malformed: a corrupt binding must never be silently downgraded to
"unbound" and thereby skip verification.

## `expected_dirty`

Repository analysis tasks are read-only over whatever state the operator's
checkout happens to be in, so their generated tasks use `expected_dirty: any`.
That relaxes the cleanliness requirement **only**. The worktree realpath, the
repository realpath, and the HEAD are still pinned — which is exactly what the
wrong-checkout review needed and lacked.

## Compatibility

`SCHEMA_VERSION` is deliberately unchanged. Adding a schema module is additive
and not a breaking change to `EventEnvelope` or any existing schema; the
version is pinned cross-repository and bumping it for an additive module would
break every consumer's contract test for no benefit.

## Implementation

- Contract: `src/assistx/contracts/schemas/repository_source_binding.py`
  (dependency-free: `pydantic` only, so it is cross-repo importable without
  pulling in executor, Neo4j, or router state)
- Verifier: `src/assistx/repository_source_verifier.py`
- Call site: `src/assistx/repo_task_generator.py`
  (`_repository_root_for_worktree`, `_source_binding_payload`, and the analysis
  and improvement-proposal payloads)
- Tests: `tests/test_repository_source_binding.py`