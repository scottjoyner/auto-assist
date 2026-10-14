# P0 canonical repository source-binding reconciliation — October 13, 2026

**Draft-only, stacked on PR #353.** This reconciles two implementations
that had diverged from the strict `RepositorySourceBinding` schema.

## Actual defect and correction

Before this patch, the older `contracts.repository_source_verifier`
builder passed deleted fields `repository_realpath` and
`dirty_expectation`, plus nullable task/work IDs, into the strict schema.
The verifier also dereferenced those obsolete fields. Consequently a
legitimate registered worktree could fail to produce the required binding,
and 26 source-binding tests were red.

The builder now requires explicit task and work IDs and emits the canonical
`repo_realpath`, `worktree_realpath`, `expected_dirty`, `branch`, `head_sha`
and identity fields. It still checks the requested worktree belongs to
the configured repository root and is explicitly registered in `git
worktree list`. No substitute mirror search or fallback is added.

The delegated source verifier reads only the canonical fields, checks
repository → worktree → branch/HEAD → dirty state in a stable order,
rejects missing bindings rather than treating absence as an authenticated
MATCH, and preserves the existing signed completion policy. Non-repository
legacy contracts remain unbound at the contract layer, not granted authority
by this verifier. The reserved `DETACHED` branch marker explicitly allows
detached observations; HEAD and repo/worktree identity still match exactly.

The repository analysis-task generator now attaches a stable provenance
`task_id` and source-snapshot `work_id` before attempting to mint the
binding; a real registered Git checkout must produce a non-null binding.
Minting still confers no approval, dispatch, mutation or failover privilege.

Filesystem root `/` is rejected as a source. Home-directory rejection
compares the realpath to catch symlinked `HOME`, as observed on x1-370.
The test for obsolete HEAD-only `improvement_runtime` payloads now verifies
the modern strict source contract: an incorrect bound HEAD is rejected
*before* worktree creation rather than merely logged as a warning.

## Validation and caveats

- x1-370 `pytest` source-binding, generator, improvement-runtime, signed
  approval and LLM integrity suite: **166 passed / 2 deselected**.
- All 40 historical repository-source-binding tests passed with updated
  canonical fixture fields and preserved negative-match assertions.
- Real registered Git fixture and generator fixture verified exact task/work
  IDs, repo/worktree path and HEAD.
- Local recovery-canary against disposable Neo4j: **1 passed**.
- Ruff targeted safety checks passed with documented preexisting style
  suppressions: `I001,F401,UP012,UP042` on older source files. Full-file
  unsuppressed Ruff was not claimed green.
- An unrelated test starts the work-supply arbiter import thread when no
  Neo4j credentials exist, generating a local pytest warning. No valid
  production Neo4j connection was attempted.
- Two API-import tests were deliberately deselected from local combined
  regression because this host does not have `langgraph` installed. Hosted
  CI must execute the complete suite with its pinned dependencies.

**No prod DB, roles, passwords, schema, network, models, router admission,
approval renewal or automatic failover changed.** The P0 B01/B02/B06
and runtime projection release gates remain HOLD. A successful hosted
test is evidence for this code slice only, not production graph custody.
