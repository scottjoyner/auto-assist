# Outcome-filter release acceptance — isolated staging evidence
**Date:** October 8, 2026 EDT. **PR:** auto-assist draft #122 stacked on UI draft #121. **Status:** synthetic browser and synthetic graph acceptance partially achieved, production deployment still **NOT APPROVED**.

## Test design and preregistration
The benchmark prospectus `TRACE_GLOBAL_PERF_PROSPECTUS_20261008.md` was recorded before creating the staging graph. The actual candidate code was already frozen on draft PR #122. Browser tests were written after identifying an installed cached Chromium/Playwright runtime. Both additions use completely synthetic source data.

## 85,000-group isolated graph results

A disposable `neo4j:5.23.0` **community** container, deliberately *not* the running 5.26 fleet instance, was started with `--network none`, no published ports, no host bind mounts, 1 CPU, approximately 2.15 GiB memory and limited PIDs. It was reached **only by `docker exec`**. We used 85,000 invented TraceGroup nodes, two invented TraceEvent nodes per group (170,000 events total), a deterministic `i mod 5` outcome distribution, and **no real trace IDs, device names, payloads, credentials, or provider data**. All nodes and relationships were on the **disposable test DB only**.

The synthetic insertion step completed in approximately **10,041 ms**, as measured around `cypher-shell`. All three outcome counts returned the expected exact results: **34,000 failed**, **34,000 completed**, and **17,000 open**, including mixed failure/completion where failure takes precedence.

### Three trials per count/page (process wall milliseconds)

| Outcome | Count samples | Count median | Page samples | Page median |
| --- | --- | ---: | --- | ---: |
| failed | 3,193; 2,529; 2,508 | 2,529 | 2,860; 2,600; 2,419 | 2,600 |
| completed | 2,682; 2,437; 2,522 | 2,522 | 2,888; 2,714; 2,441 | 2,714 |
| open | 2,426; 2,318; 2,445 | 2,426 | 2,344; 2,482; 2,537 | 2,482 |

**These numbers include JVM startup, Docker exec and Cypher-shell overhead**, so they are *not isolated Neo4j server latency*. A 4-second query timeout was encoded in the production Python `neo4j.Query` contract and tested by mocks; the CLI benchmark did **not** reproduce that exact driver's timeout/admission behavior. Therefore **no production p95/p99 or actual driver SLA** can be claimed from three CLI timings per variant.

`EXPLAIN` statements ran, but the `cypher-shell --format plain` output only surfaced top-level metadata (`READ_ONLY`, Neo4j 5 planner/runtime, wall/plan time) and **did not include an operator tree or DB-hit estimates**. Thus the index/plan acceptance gate remains **OPEN**. The staging container used Neo4j **5.23.0**, whereas the available production-pinned image is 5.26; that version mismatch further limits inference. We did **not** run any query against the live Neo4j service, and the isolated container and its anonymous Docker volumes were removed immediately after the benchmark.

Reproduction source: `tests/bench_trace_global_neo4j.py`, including fixed-synthetic Cypher fixture and the same count/page query strings captured from actual `list_traces`. A **post-run guardrail update** prevents the script from seeding any container unless its known test name, network=none, no published ports/bind mounts, image version, no auth, memory and CPU caps match; and checks expected synthetic outcome counts. The guardrail changes were static-checked and deliberately not rerun against a second graph. The original measured trial ran under manually inspected equivalent isolation and was cleaned up.

## Real Chromium synthetic browser acceptance

A previously installed real Chromium binary with Playwright JavaScript and axe-core was reused; no software downloaded, no production browser authenticated, and no live API responses opened. A Playwright route intercepts **all** page/API/static requests to a synthetic `test.assistx.invalid` origin, serving the candidate HTML/CSS/JS and invented trace events only. If code attempts a different origin, the test aborts.

**5 of 5 headless browser tests passed**, covering:
- **375px mobile:** no horizontal document overflow; filtered direct link for a trace not on the first page; selection, keyboard focus, safe collapsed event-field disclosure; event-field data cleared on re-collapse; staged provider link not advertised as live.
- **1440px desktop:** two side-by-side investigation panels without overflow; changing global outcome updates counts and URL state.
- **401 auth failure:** explicit unavailable state and **unknown** counts, not misleading zeros.
- **Older backend:** filter-acknowledgment absence is reported explicitly with unknown counts.
- **Automated axe WCAG 2.1 A/AA scan:** **zero reported violations** on the mobile synthetic fixture.

Source: `tests/test_trace_browser_acceptance.cjs`. The tests need a local installed `playwright`, `@axe-core/playwright` package and suitable Chromium binary (on x1-370, Playwright can be resolved through a separate local OmniRoute tool install). The tests do not require frontend deployment, database connections, provider credentials or real operational trace text. Synthetic screenshots were saved only under `/tmp` on x1-370 and were not committed.

## Acceptance summary and remaining blockers

| Gate | State |
| --- | --- |
| Python query/FastAPI static contract tests | 17 passed (previous slice) |
| JS UI VM interaction tests | 11 passed (previous slice) |
| Real Chromium synthetic layout/disclosure/auth/deep link | **5 passed** |
| Axe mobile automated WCAG 2.1 A/AA | **0 reported violations**, manual accessibility outstanding |
| Full synthetic 85k-group outcome membership/count | **Exact expected totals observed** |
| CLI wall-clock timing on isolated Neo4j | **Measured** but includes startup, not production SLA |
| Actual Cypher operator plan, DB hits, production indexes | **Not established** |
| Driver 4s timeout under load, 1/5/10 concurrent users, p95/p99 | **Not established** |
| Authenticated deployed browser + user input/privacy review | **Not established** |
| Full-fidelity historical tool-call retention | **Separate open issue #117** |

**Decision:** retain both PRs as **draft**. The next narrow slice should improve graph query-plan evidence with a consistent Neo4j 5.26 staging setup and persistent driver benchmarking, plus keyboard/screen-reader testing against a verified authenticated deployment under operator review. No deployment, production Neo4j changes, NAS writes or provider routing changes have been made.
