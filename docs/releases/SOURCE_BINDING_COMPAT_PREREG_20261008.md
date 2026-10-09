# Source-binding strict compatibility repair — preregistered October 8, 2026

Base auto-assist main at a300072d11787a58a4587fa325f1b8fd66803730.
Research/release safety scope: repair broken contract consistency, **not**
grant execution, routing, provider, model, quota, filesystem writes or task
claims. This is a separate draft from offline RC #136.

Observed baseline BEFORE patch: 29 FAIL / 11 PASS in
tests/test_repository_source_binding.py with --noconftest.
GitHub CI recovery-canary also failed on AttributeError:
RepositorySourceBinding.from_contract_payload.

### Root cause and predicted result
Existing consumers disagree on canonical names. The source-binding schema
expects repo_realpath / expected_dirty while contracts/repository_source_verifier
and its tests expect repository_realpath / dirty_expectation. The schema
requires task_id and work_id while bound-source creation allows these to
be optional; both to_contract_payload and from_contract_payload are missing;
SourceBindingState lacks UNBOUND and BRANCH_MISMATCH, and DirtyStateExpectation
lacks DIRTY_ALLOWED. This is not a safe place to bypass verification.

**Prediction:** A strict bidirectional naming adapter with forbid-extra/frozen
schema will restore 40/40 source-binding cases, including failures for
wrong source, stale mirror, wrong SHA, dirty source, missing source, or
unexpected payload fields. Invalid *present* binding still raises;
absent/empty optional binding remains unbound for legacy tasks as explicitly
documented. Every serialization/parse path must preserve canonical identity,
exact task/work IDs when provided, and no fallback source. No new authority
flags or producer/dispatch changes are permitted.

### Planned tests
PYTHONPATH=src python3 -m pytest --noconftest -q
tests/test_repository_source_binding.py

Then run its dependent strictly offline improvement-cycle fixtures. Do not
run the Docker-backed recovery integration canary as part of local checking,
and do not claim GitHub CI is green until remote workflows confirm.

### Observations and final focused safety gate

- Initial root baseline: 29 failed, 11 passed, from 40 existing
  source-binding tests against unchanged auto-assist main.
- Source schema now accepts both explicitly known wire names, emits the
  legacy repository_realpath / dirty_expectation names, rejects ambiguous
  duplicate aliases, retains extra=forbid/frozen and strict canonical source
  checks; no runtime or authorization interface was modified.
- Home-directory symlinks needed a further strict negative check: raw $HOME
  and its resolved realpath are both denied as repository/worktree roots.
- Final focused source-binding tests: **56/56 PASS** (40 existing + 16 new
  parameterized security/wire assertions), 0.57 seconds.
- The wider optional improvement suite returned 66 PASS and 5 FAIL during
  exploration, including two test assertions initially over-strict about
  expected default metadata (subsequently corrected and passing in the 56).
  Remaining three failures are separately scoped: two optional API tests
  unable to import prometheus_client in this local environment, and one
  improvement_runtime assertion expecting source_binding_head_sha absent
  from current main. These are NOT claimed remediated.
- GitHub's recovery integration canary previously failed on the missing
  from_contract_payload method. This source fix removes that specific
  AttributeError, but an actual Docker/Neo4j recovery test has NOT been
  run in this source-binding worktree. CI must independently verify.
- No provider API, hosted model, filesystem search fallback, source alias
  trust widening, command execution, claims, or task/routing authority
  changes were made. Keep this branch DRAFT until CI evidence and review.
