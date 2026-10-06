# Repository Source Binding

**Status:** implemented, advisory at adoption, fail-closed once bound.
**Scope:** source identity only. No claim, execution, write, dispatch,
approval, or routing authority is granted or changed by this contract.

## The failure this closes

A Hermes swarm reviewer was delegated a review of a dedicated worktree:

```
/media/scott/SSD_4TB/worktrees/auto-ingest-swarm-20261002
```

It inspected a different directory:

```
/home/scott/embed_x1
```

The review it returned was well-formed, confident, and about source that was
never requested. Nothing in the result marked it as such, so the review had to
be rejected by a human after the fact.

The root cause is not that the reviewer was careless — it is that the task
contract said nothing about *which checkout* counted. With no bound source, any
directory that looks enough like the repository is an acceptable substitute,
including a stale mirror, and a result carries no evidence of which one was read.

## The contract

`RepositorySourceBinding` (`src/assistx/contracts/schemas/repository_source_binding.py`)
names the one acceptable source for a repository-bound task or delegation:

| field | meaning |
| --- | --- |
| `repository` | opaque identity/alias (never a path) |
| `repository_realpath` | canonical realpath of the repository that owns the object database |
| `worktree_realpath` | canonical realpath of the exact worktree under work |
| `branch` | expected ref name, or `null` when branch-agnostic |
| `head_sha` | expected full commit SHA |
| `dirty_expectation` | `clean_required` (default) or `dirty_allowed` |
| `task_id` / `work_id` | task or delegation the binding was minted for |
| `source_manifest_sha256` | optional digest of a source manifest |

It is a *frozen*, `extra="forbid"` pydantic model. Validation is deliberately
strict, because **a caller-supplied host path is never authoritative merely
because it was supplied**:

- `repository` must be an alias (`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`). A path
  in the identity field is rejected.
- both paths must already be canonical absolute realpaths: no relative paths, no
  `..`, no trailing slash, no whitespace padding, no control characters.
- `$HOME` and filesystem roots are rejected as bound sources. `$HOME` is the
  canonical silent fallback, so it can never be the answer.
- `head_sha` must be a full lowercase hex commit SHA; a short/abbreviated SHA
  cannot satisfy a binding.
- unknown fields are rejected, so a `fallback_path`-style escape hatch cannot be
  smuggled in through the binding.

`RepositorySourceBinding.from_contract_payload()` returns `None` only when no
binding is present at all. A *present but invalid* binding raises — a malformed
binding is never silently downgraded to "unbound", because that would skip
verification precisely when it matters.

## Minting a binding

`build_source_binding()` (`src/assistx/contracts/repository_source_verifier.py`)
mints a binding from an **observed** source, and requires two things the caller
cannot forge:

1. `base_repository_path` — the configured canonical root for that repository.
2. `worktree_path` must be that base root, or a worktree git reports as
   *registered* for it (`git worktree list --porcelain`).

So a sibling directory that merely shares a name, an independent clone, or a
mirror cannot be bound. Minting a binding for them raises.

## Verification

`verify_repository_source(binding, observed)` is a pure comparison — no
filesystem or git access — so the rule is deterministic and unit-testable.
`verify_source_workspace(binding, path)` observes a path and then compares.

States, in evaluation order:

| state | meaning | accepted |
| --- | --- | --- |
| `MATCH` | expected repo + worktree + branch + HEAD + dirty state all observed | yes |
| `SOURCE_UNAVAILABLE` | the requested source could not be read at all | no |
| `REPOSITORY_MISMATCH` | a different repository owns the object database | no |
| `WORKTREE_MISMATCH` | right repository, different worktree | no |
| `BRANCH_MISMATCH` | bound branch not observed | no |
| `HEAD_MISMATCH` | right worktree, stale HEAD | no |
| `DIRTY_STATE_MISMATCH` | `clean_required` but the worktree is dirty/unreadable | no |
| `UNBOUND` | no binding declared (legacy or non-repository task) | yes |

Repository identity is checked before worktree identity on purpose: a clone of
the "same repository" with the same directory name is a *different repository* as
far as source identity is concerned, and should be reported as such.

### A mismatch is terminal, not a hint

The verifier never searches for a substitute. On any mismatch it does **not**:

- try another checkout of the same repository;
- try another worktree of the same repository;
- search by repository name;
- fall back to `$HOME`;
- fall back to `/home/scott/embed_x1` or any other mirror.

There is exactly one acceptable source. Everything else is a mismatch, and a
mismatch is a rejection the caller must surface — not resolve by looking
elsewhere.

## Where it is enforced

| call site | behaviour |
| --- | --- |
| `improvement_cycle.build_execution_contract(..., source_binding=...)` | binds the contract; the binding's `repository` must equal the contract's `repository`. Omitting it leaves legacy contracts byte-identical. |
| `improvement_cycle.build_work_packet` | hands the worker the binding plus an explicit "do not substitute another checkout" requirement. |
| `improvement_runtime.prepare_repository` | verifies the configured base against the binding *before* creating an isolated worktree; on mismatch returns `ok=False` with `repository_source_binding_rejected:<STATE>`. |
| `improvement_runtime.collect_executor_evidence` | records `observed_source` + `source_binding_verification` in the signed evidence. |
| `improvement_runtime.promote_patch` | re-verifies before applying a patch to the configured root. |
| `improvement_cycle.evaluate_completion` | a bound task whose completion evidence lacks a `MATCH` source verification is rejected (`repository_source_binding_unverified` / `..._rejected:<STATE>`). An unbound task is unaffected. |
| `improvement_cycle.ImprovementCycle.propose_repair` | carries the binding forward so a repair targets the same source instead of re-resolving one. |
| `repo_task_generator._source_binding` | repository analysis tasks record the binding in their payload, so a reviewer result can later prove what source it examined. |

### Provenance

The verdict is designed to be carried, not logged and forgotten. Both sides are
recorded: `expected` (the full binding) and `observed` (repo realpath,
worktree realpath, HEAD, branch, dirty). That is what lets a reviewer result
prove what it examined, and lets a rejected review be diagnosed as
`REPOSITORY_MISMATCH` rather than argued about.

## Compatibility

Tasks without a binding are unchanged. `UNBOUND` is reported with
`accepted=True`, contracts built without `source_binding` contain no
`source_binding` key, and `evaluate_completion` applies the gate only when a
binding is actually present. Non-repository tasks are therefore unaffected.

Adopting a binding is opt-in per contract: repository-bound callers add one; the
platform does not silently mint bindings for work that never declared one.

## Tests

`tests/test_repository_source_binding.py` covers the required matrix against both
synthetic observations and real temporary git repositories (a main checkout, two
registered worktrees, and an independent clone with the same directory name):

| case | expected |
| --- | --- |
| exact requested worktree + HEAD | `MATCH`, accepted |
| same repo / different worktree | `WORKTREE_MISMATCH`, rejected |
| same worktree / stale HEAD | `HEAD_MISMATCH`, rejected |
| different mirror with same repo name | `REPOSITORY_MISMATCH`, rejected |
| missing worktree | `SOURCE_UNAVAILABLE`, rejected |
| non-repository task | `UNBOUND`, unaffected |

plus: path-shaped identities rejected, non-canonical/traversable paths rejected,
`$HOME` and `/` rejected, unregistered sibling directory cannot be bound, mirror
cannot be bound, and a bound completion is rejected when its provenance is
missing or reports a mismatch.
