# PR #119 controlled `main` baseline evidence — 2026-10-08

**Decision:** At the exact reviewed revisions, broad CI remains red but none of its 77 failing unit-test identities or one recovery-canary failure are introduced by PR #119. This is failure *attribution* evidence, **not** a waiver, green required check, merge approval, or execution authority.

## Source revisions and runs

| Source | Exact revision | GitHub Actions run | Unit result | Recovery canary |
| --- | --- | --- | --- | --- |
| Original PR base `main` | `a300072d11787a58a4587fa325f1b8fd66803730` | [37849339659](https://github.com/scottjoyner/auto-assist/actions/runs/37849339659) | 669 passed, 77 failed, 59 deselected | 11 passed, 1 failed |
| Isolated control branch with **documentation-only** baseline commit | `382587fb2ce8a1948c1736c53a32df0b9033a573` (parent exactly `a300072d`) | [37849342657](https://github.com/scottjoyner/auto-assist/actions/runs/37849342657) | 669 passed, 77 failed, 59 deselected | 11 passed, 1 failed |
| PR #119 | `56c79615e4ea4055369f6de1a0fe92eb5dca9980` | [37847395613](https://github.com/scottjoyner/auto-assist/actions/runs/37847395613) | 866 passed, 77 failed, 59 deselected | 11 passed, 1 failed |

## Comparison method and findings

Examined the exact `FAILED tests/...` node IDs in **unit job logs**, and compared the sets between all three jobs. Each unit run had **77 distinct failed test IDs**. There were **0 PR-only failures**, **0 base-only failures**, **0 baseline-branch-only failures**, and all 77 IDs matched exactly. The one failed `tests/test_recovery_canary.py::test_recovery_lifecycle_canary_with_real_neo4j` matched across all three runs. PR unit pass counts include more trace-focused and regression tests and therefore are **not** a same-denominator success-rate improvement.

The baseline branch `research/pr119-main-ci-baseline-20261008` was created at the exact base commit. Its sole extra commit adds `docs/ci-baseline-pr119-20261008.md`, leaving test and application code unchanged.

## Concrete release decision

- **Baseline attribution:** matched and reproducible, but does not waive failing required CI.
- **Dedicated trace acceptance:** [run 37847395515](https://github.com/scottjoyner/auto-assist/actions/runs/37847395515) at `56c79615` passed 213 focused tests, compile, lint, and independent JUnit/report digest verifier; fixture gates 7/8; no production activation gates passed.
- **Keep PR #119 draft**; production issuer, key custody, revocation fencing, independent audit/WORM, private NAS destination and authenticated physical negative admission remain blocked.
- Retain all original evidence, do not modify production flags, dispatch tasks or write to NAS.
- Next broad-CI work should address source-binding contract mismatches, dashboard projections, integrity regressions, missing fixtures and recovery-canary failure as independent scoped repairs. Do not make unrelated API changes on this trace security branch merely to force an aggregate green result.

Recorded 2026-10-08 (UTC), from GitHub Actions jobs; comparison was based on failed-case identifiers rather than just counts.
