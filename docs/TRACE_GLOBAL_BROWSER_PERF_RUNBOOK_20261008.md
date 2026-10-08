# Real browser + bounded synthetic Neo4j acceptance runbook

**Scope:** Read-only AssistX trace outcome UI acceptance for [draft PR #122](https://github.com/scottjoyner/auto-assist/pull/122). All records generated; no live fleet Neo4j, API, private trace payloads, model endpoints, NAS or shared collectors.

## Dependency-free contract tests
```bash
cd /home/scott/git/wt-assistx-trace-global-filter-20261008
PYTHONPATH=src python3 -m pytest -q tests/test_trace_outcome_filter.py
node --test tests/test_trace_investigation_ui.cjs
```
Expected at the October 8 checkpoint: 17 Python and 11 Node passes (the 11 already include the prior seven UI cases).

## Actual Chromium synthetic viewport, authentication and WCAG tests
On x1-370 there is an existing Playwright install alongside OmniRoute, a Chromium cache, and the axe-core Playwright integration. The test script fails if any request tries to leave the synthetic fixture origin.

```bash
NODE_PATH=/home/scott/git/OmniRoute/node_modules \
CHROMIUM_PATH=/home/scott/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome \
node --test tests/test_trace_browser_acceptance.cjs
```
**5/5 passed:** 375px mobile no overflow and disclosure privacy; 1440px desktop layout/filter navigation; 401 explicit unknown counts; older backend fail-closed; axe WCAG 2.1 A/AA mobile synthetic scan with zero detected violations. Screenshots from invented data are at /tmp on x1-370, not uploaded. This is not a genuine authenticated production browser audit, manual keyboard sequence or independent screen-reader sign-off.

## Synthetic 85k graph benchmark (resource bounded, owner-only)
**Warning: this deliberately performs *synthetic writes* into a new disposable staging container. Never run it against the production Neo4j database.** The script has a fail-closed preflight requiring the exact container name, version, no network/ports/bind mounts and CPU/memory caps. On x1-370 the image neo4j:5.23.0 was available locally. Using --network none required an explicit hostname /etc/hosts mapping; otherwise Java cannot resolve its own hostname and Neo4j exits.

Example isolated staging preparation, no host-mapped ports:
```bash
docker run -d --name assistx-tracebench-stage-20261008 \
  --hostname tracebench-only --add-host tracebench-only:127.0.0.1 \
  --network none --cpus 1 --memory 2200m --memory-swap 2200m --pids-limit 256 \
  -e NEO4J_AUTH=none neo4j:5.23.0

# Require successful isolated readiness. Do NOT replace these with production creds.
docker exec assistx-tracebench-stage-20261008 \
  cypher-shell -a bolt://localhost:7687 'RETURN 1 AS healthy'

PYTHONPATH=src python3 tests/bench_trace_global_neo4j.py

# Always remove only the disposable staging container and anonymous volumes:
docker stop --time 5 assistx-tracebench-stage-20261008
docker rm -v assistx-tracebench-stage-20261008
```

**Checkpoint result:** 85,000 generated trace groups / 170,000 synthetic event nodes; exact outcome totals 34,000 failed / 34,000 completed / 17,000 open. Three Cypher-shell count and three page calls per category had process wall medians around 2.4–2.7 seconds, **including JVM startup and Docker exec**. Neo4j driver transaction timeouts and production p95 are NOT measured. `EXPLAIN` output included only top-level metadata without a detailed operator tree, so it cannot prove DB hits, indexes or query-plan efficiency. Version 5.23.0 staged; a 5.26 production-version validation is still pending. This does not close [release gate #123](https://github.com/scottjoyner/auto-assist/issues/123).

Read `TRACE_GLOBAL_PERF_PROSPECTUS_20261008.md`, `TRACE_GLOBAL_STAGE_OBSERVATIONS_20261008.md`, and `trace_global_perf_synthetic_results.json` for predictions, observations and machine-readable synthetic evidence. Both UI PRs remain draft and production unchanged.
