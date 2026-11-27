# Starred ripwire: sandboxed, no-execution dependency/test-gate pilot

**Status: RESEARCH / production NO-GO** (2026-11-02, x1-370).

## Exact inputs

- `redhat-et/ripwire` release **v0.6.9**, Apache-2.0; Linux x64
  release archive SHA-256:
  `5444af4124bec689c36a65879b44389dd18fda9fd4ad2037369fc6d27625080a`.
- Test source: public `google-research/harness2` at full commit
  `6868817518876de8fd170c6a8354daca273b6191`.
- Source file under simulated review: `harness2/core/schedule.py`.
- All inspection invoked the release `ripwire` **directly**, not the
  upstream installer. The archive was verified and inspected for unsafe
  symlink/link/path-traversal members first.
- Executable was run through an ephemeral Docker container with
  `--network none`, `--read-only`, `--cap-drop ALL`,
  `--security-opt no-new-privileges`, unprivileged UID,
  read-only mounts, 1-GiB memory/1-CPU/64-PID caps, bounded `/tmp`,
  and a 25-second timeout. All directories cleaned on exit.
- **No production repository**, private source, live traces, credentials,
  resident daemon, agent skills/hook installation, model load,
  branch merge, or fleet dispatch was involved.

## Results: source-only static analysis, not proof of coverage

With `ripwire . --json --test-gate=harness2/core/schedule.py`,
the binary exited **4**, which its test-gate contract uses to flag
tests/untested impacted symbols, *not* to signal a successful security
approval. It emitted a 3,079-byte advisory JSON object:

| Field | Observed |
| --- | ---: |
| Reported impacted-symbol lower bound | 86 |
| Test file suggestions | 9 |
| Test suggestions without known runner | 3 |
| Reported untested impacted symbols | 40 |
| Graph-ambiguous edges | 21 |
| Graph-unresolved edges | 12 |
| Unindexed files | 7 |
| Declined calls | 13 |

The graph can be incomplete. Even a list of relevant-looking tests
cannot justify selecting **only** those tests for CI/release gating.
Recommended commands found in `run` and `next` are untrusted strings:
**never execute them from the report**.

## Proof-of-concept adapter

`scripts/experiments/ripwire_shadow_test_gate.py` is standard-library
Python. It reads a bounded locally produced upstream JSON report and a
Git working tree, and verifies test paths, grouping and count consistency.
It never starts subprocesses or imports the upstream binary. Its default
report includes only counts, a deterministic digest of sorted test names,
and risk/status flags, not filenames or upstream command strings.
An explicit `--show-local-paths` is available for a *manual local*
inspection of validated relative paths.

The output always preserves:

- `coverage_complete=false`,
  `test_coverage_independently_verified=false`;
- `full_test_suite_still_required=true`;
- `dispatch_allowed=false`, `harness_change_allowed=false`;
- `production_gate=HOLD`.

It marks ambiguity, missing graph coverage, declined links and capped
output as `selection_incomplete_or_uncertain=true`. It does not verify
the checkout source SHA independently (the field is marked *claimed*),
does not prove source graph completeness, and cannot authorize test
omission or production changes.

## Executed validations

- SHA-pinned standalone adapter at commit
  `e2c21d42bf2b7e447fabbba8f70da5bdc863bf60`:
  **20/20** focused synthetic/adversarial unit tests PASS;
  Python AST parse PASS.
- The **real sandbox-produced JSON** parsed successfully through the
  same adapter. It reported nine bounded valid suggested tests,
  three unknown runners, graph uncertainty, and production HOLD.
- No recommended tests were executed, so there is **no demonstrated
  test-selection precision/recall** or improvement in useful
  task-completion rate. The counts are static analysis results.

## Next gates

1. Re-run against a fresh synthetic repository with known caller/test
   ground truth. Measure misses and false positives, including dynamic
   dispatch, import-only links, symlinks, stale file changes and aliasing.
2. Compare against a full isolated suite and `rg` baseline. Collect
   total repository analysis wall time, RSS, cache size, and cold/warm
   context bytes, not vendor claims.
3. In any future Hermes/PI adapter, treat suggestions as bounded
   **discovery hints**, not automatic execution instructions. Ensure
   signed task/claim IDs, attested runtime, approved tool scope and
   AssistX admission remain separate.
4. Verify independent security, storage and licensing gates before any
   install, daemon, remote MCP server, plugin, model workload or merge.
   Keep production NO-GO until the existing AssistX/Auto-Router fences
   and SGNC custody requirements are independently satisfied.
