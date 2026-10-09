# RC2 baseline CI: source-binding expectation reconciliation
Date: October 8, 2026 EDT. Base: draft RC2 #155.

## Hypothesis and prior evidence
The remaining `test_workspace_records_whether_the_binding_matches_the_executed_head` failed because it asserted undocumented `source_binding_head_sha` / `source_binding_matches` fields and that an executor may proceed on a source-binding mismatch. The current verified `RepositorySourceBinding` contract instead requires a configured repository alias, exact repository and worktree realpaths, and HEAD equality; mismatch must deny before creating any worktree. Retrofitting permissive warning-only behavior to make the test pass would defeat the source-integrity security boundary restored in draft #144.

## Bounded correction
- Replace the stale fixture with a canonical fully specified test binding, including the configured repository alias, exact resolved paths, 40-character HEAD and clean expectation. The test uses only a disposable temporary Git repository.
- An unbound contract remains valid but has `source_binding_verification=None`; its absence is not evidence of a match.
- A correctly bound task returns `source_binding_verification.state=MATCH` and `accepted=true`, with exact observed HEAD.
- A valid binding naming a different HEAD now proves `repository_source_binding_rejected:HEAD_MISMATCH` and `accepted=false` without creating a new worktree.
- Worktrees for successful fixture executions are cleaned within the test. No changes to implementation, cryptographic claims, source verifier, task allocation, execution privileges, provider routing or real repositories.

## Observed results
- Isolated x1-370 checkout based on RC2 `0e7e09f80c9686bf968d6c1c60f24b1b39843c3e`. The original broad CI showed KeyError for old fields.
- After replacing only the test, `PYTHONPATH=src python3 -m pytest -q tests/test_improvement_runtime.py tests/test_repository_source_binding.py` returned **46 passed, 0 failed**, and `git diff --check` passed.
- GitHub full CI remains a distinct gate. Other baseline failures, including missing secret-file custody #156, runtime routing and dashboard projection contracts, remain unclosed by this change.

Decision: retain fail-closed source binding; keep the test-only PR draft pending exact SHA CI. Do not deploy or merge runtime authority from this research.
