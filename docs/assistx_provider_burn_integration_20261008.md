# AssistX fleet provider burn integration — implementation and rollout gate

**Date:** 2026-10-08. **Status:** source-wired and locally tested; NOT deployed to live AssistX.
**Scope:** read-only operator reporting only. No provider API calls, generation, task dispatch, lease release/acquire, automatic quota grants or routing changes.

## UI and endpoint
- Authenticated GET `/provider-usage` renders `templates/provider_usage.html`.
- Authenticated GET `/api/fleet/provider-burn` returns a limited, sanitized `assistx-provider-burn/v1` projection of a local precomputed `free-endpoint-burn-dashboard/v1` file.
- Fleet dashboard, trace viewer and common shell navigation link to the new page. Existing trace history remains canonical; token accounting does not assert delivered work or trace-to-commit attribution.
- Read-only API returns `Cache-Control: private, no-store`; requires AssistX's existing Basic/trusted-header auth dependency.
- The API **does not** read OpenCode SQLite or call an inference provider. The separate collector processes SQLite offline. No raw session IDs, source DB paths, credentials, prompts or tool outputs are included in the API response.
- Missing, malformed or oversized snapshots return `state=unavailable`; old ones `state=stale` after 1 hour. Partial source coverage never produces a false accepted/verified top-line total.
- Vendor allowance/remaining quota stays `null` and free entitlement `false`. Flat-pace estimates are observed token volume, not vendor billing units.

## Snapshot location contract
- Compose config declares `ASSISTX_FREE_BURN_REPORT_PATH=/app/artifacts/free-endpoint-burn.json`.
- This is the **existing AssistX API artifacts bind mount**, not a new external API. Collector must publish a tightly scoped, private report to its corresponding host artifact directory, with container-readable permissions, after verifying actual deployment custody.
- The collector source is `scripts/free_endpoint_burn_dashboard.py`; it can produce JSON without model calls. A real-time page request only loads a precomputed JSON file. The host producer refresh interval and file ownership must be reviewed before installation.
- The source-only preview can use an explicit `report_path` or `ASSISTX_FREE_BURN_REPORT_PATH`. No timer is installed by this branch.

## Live deployment blocker discovered
The running `assistx-api` container uses image `auto-assist-wt-ab3bb64-api`; its live Python/templates are bind-mounted from `/home/scott/git/auto-assist-wt-ab3bb64/`. That directory is a **non-Git deployment snapshot**, not the isolated feature branch or `/home/scott/git/auto-assist`.
The static JavaScript directory is baked into the Docker image, not host-mounted. Updating just mounted Python/templates would leave the new JavaScript missing. Rebuild or a reviewed asset delivery mechanism is required.
**Do not copy the feature branch's entire API router or templates into the deployed snapshot.** First reconcile deployed source hashes, current recovery fences, routing authority and executor state. Preserve the deployed snapshot and unrelated work.

## Minimum safe release/rollback protocol (not yet executed)
1. Review changes from branch `feat/free-endpoint-burn-dashboard-20261008` against the current deployment snapshot; stage a new immutable image while keeping existing container and volumes intact. Avoid inadvertent changes to executor/safety fences.
2. Generate a sanitized, reproducible local report and confirm the container runtime identity can read it from the existing artifacts mount **without widening the mount, chmodding it world-readable, or exposing host SQLite**. Refuse deployment when ownership/permissions are unresolved.
3. Check `/api/fleet/provider-burn` without credentials → 401; authenticated request → schema, freshness, coverage; `/provider-usage` → 200 and JS loads; missing snapshot → explicitly unavailable; trace links remain correct.
4. Check existing `/health`, `/fleet-dashboard`, `/traces`, runtime projection/authority fences, and executor task continuity before/after a controlled API image swap. Do not increase provider concurrency.
5. If smoke checks fail, restore the original image/container and original sources, leaving artifacts and shared state intact. Record commit, image digest, timestamp, evidence and disposition in the knowledge repo.
6. Only after this acceptance, separately enable a 15-minute read-only collector schedule with atomic snapshot publication, audit retention and service health checks. Keep provider-generation experiments and lease admission under the separate operator-approved gate.

## Tests and limitations
```bash
python3 -m pytest -q tests/test_free_endpoint_burn_dashboard.py tests/test_provider_burn_routes.py
```
The local integration suite includes auth denial, sanitized response, Jinja page, stale/missing and partial snapshot behavior, and offline token accounting. It is **not** a substitute for a live-container security or deployment smoke test.
Future work: add verified quota ceilings/reset times and shared physical upstream IDs from credential-safe provider billing APIs, plus task/commit/test acceptance receipts for useful work per token. These must not be inferred from free-model names, basic catalog reachability or historical execution-score counters.


## October 8 source validation
- New collector/API route tests: **10 passed**. The complete historic Fleet Dashboard inference test module could not collect in the Fleet Commander Python environment: `ModuleNotFoundError: langgraph`. No packages were installed or changed to bypass this.
- Read-only replay of `artifacts/free-endpoint-burn-preview.json` through the actual AssistX projection returned 15 provider rows and 43 model rows, with private DB paths absent.
- Real API container is still running unchanged; there is no live `/provider-usage` deployment or scheduled snapshot producer yet. Do not report the feature as live.
