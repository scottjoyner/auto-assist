# Observations 11 — isolated Neo4j 5.26.30 persistent-driver acceptance

**Date:** October 8, 2026 EDT. **Preregistered hypothesis and method:** `TRACE_526_DRIVER_PROSPECTUS_20261008.md` before graph generation. The initial plan was amended **before measurement** when Docker internal bridging did not publish the requested localhost port. `EXPLAIN`/read-only `PROFILE` and synthetic fixture creation were conducted exclusively on a new disconnected disposable database.

## Experimental controls and clean teardown

Image `neo4j:5.26-enterprise` actually identified itself as **Neo4j 5.26.30**; local Python driver version **6.2.0**. Named disposable Docker container `assistx-tracebench-526-20261008` used a dedicated Docker-internal bridge `assistx-tracebench-isolated-20261008` (`Internal=true`), a single CPU, **2,200 MiB RAM**, no host bind mounts or mounted secrets, and a PID cap. The only published Bolt port was explicitly bound to localhost, but Docker did not forward it on this internal bridge. A guarded driver connected instead to the **exact Docker-inspected private test-container IP**. It never accepted an environment-provided production Bolt URL or credentials. The destination’s name, image, running state, network, resource caps, no-bind-mount policy, auth-none synthetic mode, and expected private IP were all checked before seeding. No production Neo4j process was opened by the benchmark.

The fixture consisted of **85,000 invented TraceGroup nodes and 170,000 invented TraceEvent nodes**, with IDs `synthetic-0` through `synthetic-84999`. Two events per group used a fixed modulo-5 failure/acceptance pattern. **Synthetic seed wall: 6,349 ms.** Expected group totals were exactly reproduced: **34,000 failed**, **34,000 completed**, **17,000 open** (failed dominates a mixed failed+completed pair). No real trace data, device identities, commands or user payloads entered the graph.

The disposable container and its anonymous volumes were explicitly deleted and the private Docker network removed after measurement. Read-only checks confirmed the existing `assistx-api`, `assistx-worker`, and production `neo4j` containers remained running and healthy.

## Five persistent-driver trial samples per count/page query

Numbers below represent **complete Python-driver query calls** against the disposable server, including result consumption, but **exclude cypher-shell/JVM process startup overhead**. They are *not* production p95/p99, and small-sample medians do not certify a latency SLA.

| Outcome | Global count timings, milliseconds | Count median | Page timings, milliseconds | Page median |
| --- | --- | ---: | --- | ---: |
| Failed | 1221.14 / 219.55 / 180.68 / 109.55 / 109.00 | **180.68** | 1496.15 / 424.03 / 412.56 / 361.66 / 381.41 | **412.56** |
| Completed | 898.76 / 214.74 / 205.35 / 260.25 / 221.51 | **221.51** | 1188.25 / 409.95 / 387.33 / 411.96 / 424.60 | **411.96** |
| Open | 536.01 / 259.21 / 226.04 / 299.06 / 201.41 | **259.21** | 891.54 / 310.33 / 302.48 / 301.38 / 298.77 | **302.48** |

All six queries completed and returned the expected data in these limited synthetic samples; no four-second driver timeout occurred.

## Actual Neo4j 5.26 read-only PROFILE operators and DB hits

The initial profile parser incorrectly assumed snake_case names; actual Neo4j driver dictionaries contain **`operatorType`** and **`dbHits`**. A separate **read-only profile refresh on the same synthetic graph** corrected the metadata extraction; the original five-run timing samples were not modified.

| Outcome | Global count reported DB hits | Paginated page reported DB hits |
| --- | ---: | ---: |
| Failed | **986,001** | **1,394,101** |
| Completed | **1,360,001** | **1,802,101** |
| Open | **1,241,001** | **1,462,101** |

Plans included `NodeByLabelScan`, `Expand(All)`, `Filter` and event-property access. For example the failed-count plan recorded a `NodeByLabelScan` of **85,000 groups** and multiple relationship/property operators. These database-hit totals are operator-reported statistics for this **generated** graph and have not been calibrated to existing production indexes. **The count and page queries both depend on growing historical event memberships; their cost is not proportional only to page size.** This is a scaling concern even though synthetic response times were acceptable.

## Concurrency screens

The initial preregistered bounded pass measured **one request at 829.13 ms**, and **three clients at 1,461.43 / 2,168.83 / 2,290.62 ms**, each request representing both count+page read queries. No error was observed.

A separately preregistered contention follow-on executed **three waves of five concurrent requests**, or **15 total** read-only calls, against the same 1-CPU staging graph. **15/15 succeeded; zero 4-second-query timeouts.** Full per-request wall range: **1,982.40–2,925.05 ms**, median **2,539.89 ms**. Five-client wall time is below the nominal limit on a *per individual query* but is not itself a transaction timeout observation. Fifteen requests are insufficient to estimate a trustworthy p95/p99 under variable production traffic.

## Tests and reproduction

- `tests/bench_trace_526_driver.py`: guarded source query extraction, empty-DB test, synthetic fixture, five driver samples, read-only profile, one- and three-client requests.
- `tests/profile_trace_526_driver.py`: read-only correction pass for camelCase `operatorType`/`dbHits`, no new graph writes.
- `tests/bench_trace_526_concurrency.py`: three bounded waves of five read-only clients and explicit error/timeout recording.
- `tests/test_trace_526_guard.py`: **13 tests** for fail-closed staging admissions and profile parser correctness.
- Full Python scoped suite: **41 passed** (13 new 5.26 guards + 11 earlier 5.23 guards + 17 query/route tests), one unrelated `StarletteDeprecationWarning`. Existing JS VM suite **11 passed**. Previous Chromium/axe synthetic browser suite **5 passed** (not rerun in this exact 5.26-only measurement). No local Python syntax failures.
- Machine-readable, synthetic-only aggregate results: `trace_526_driver_synthetic_results.json`, `trace_526_driver_concurrency_results.json`.
- The docker benchmark requires a specifically named private disposable container, image, address, internal network and resource caps. Its hard-coded inspected staging address belongs only to the October 8 experiment; reusing the script on a new machine requires an explicit reviewed rebinding—not a silent fallback to production.

## Decision, no-go boundaries and next improvements

**Synthetic performance/per-query timeout smoke accepted for research**, but this is **not a production UI release acceptance**. The one-CPU graph and synthetic time distribution omit real index layouts, skewed event chains, long retention tails, production traffic and database multiprogramming. The high DB-hit counts counsel against casually scaling to hundreds of thousands/millions of groups without reviewing an index/rollup strategy.

Still blocked in [auto-assist #123](https://github.com/scottjoyner/auto-assist/issues/123): production-equivalent index inspection, sustained/load p95/p99, accurate end-to-end auth/limit handling, mobile and manual keyboard/screen-reader browser testing against an **authenticated deployment**, and explicit deployment/rollback review. Newer provenance-limited PRs #124/#128 and synthetic attestation research #129 remain **separate unmerged scope**. Full historical, no-trimming tool-call archive evidence remains blocked under #117. No production trace contents, Neo4j writes, execution/admission changes, NAS modifications or real-service restarts were performed.
