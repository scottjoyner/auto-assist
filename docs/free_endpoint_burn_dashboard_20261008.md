# Fleet free-endpoint consumption and burn dashboard
**Stage:** read-only local preview, not a deployed service or live provider admission authority.
**Scope:** x1-370 account and isolated OpenCode databases; optionally reads existing provider-presence JSON. No live inference/network requests, credential reads, Git modifications to target worktrees, router admission, or service restarts.

## Generate a private snapshot
```bash
python3 scripts/free_endpoint_burn_dashboard.py \
  --out-json artifacts/free-endpoint-burn-preview.json \
  --out-html artifacts/free-endpoint-burn-preview.html
```
Outputs are mode `0600`; the HTML contains only aggregated model/provider metrics, no titles, task prompts, session IDs or credentials. The script doesn't create periodic snapshots unless separately scheduled. It doesn't serve the HTML over HTTP.

The preview shows per-provider recent 1h/24h/7d observed token totals, cache-read volume, flat-pace next-24h forecast, optional operator plan variance, catalog-only presence, and per-model 24h hourly activity. Every entry's quota remaining and verified entitlement are UNKNOWN.

## Semantics and accuracy boundaries
- Event time comes from OpenCode `part` rows where `type=step-finish`; **do not** assign cumulative `session.tokens_*` to `time_updated`. Deduplicate across the account and isolated DBs by session ID (account DB wins if the same ID occurs twice; investigate divergent mirrors).
- Token `total` is **reported model-step consumption**, not necessarily vendor billable tokens or quota debit. Model-step totals include reported cache and reasoning tokens. These may be billed or limited differently by provider.
- Forecast next 24h = last 24h observed consumption if that pace repeats. Forecast next 7d = 7 × that pace; **not** a statistical prediction, reservation, permission to dispatch, or claim about remaining free credits. Paused/no recent use forecasts zero even if prior bursts were large.
- `name_suggests_free` only observes the substring `:free` or suffix `-free`. Kilo/OpenRouter/OpenCode aliases may share the same physical provider quota or entitlement; every model's eligibility remains UNVERIFIED.
- Report completeness means the scoped DB reads succeeded without query caps. It does **not** cover other fleet nodes, direct provider APIs, remote gateways, pending streaming usage, or billing portals. Catalog health is not generation acceptance.

## Optional operator demand plan
Provide a private JSON file with the following schema and **your own planning targets**. Values are local budgets, not provider-advertised free caps:
```json
{
  "schema": "free-burn-policy/v1",
  "providers": {
    "openrouter": {"planned_tokens_per_day": 1000000}
  }
}
```
The numeric value above is illustrative, not a quota claim. Invoke with `--policy /private/path/operator-plan.json`. The dashboard compares observed last 24h tokens with that plan, allowing operators to see forecast oversubscription without initiating requests.

## Pre-registered verification and future refresh gate
**Prediction:** multiple sessions with a shared ID across account/isolated DBs are counted once; a recent step is placed in its event-hour window; a historical high-token session updated today is **not** falsely classified as all-today use; missing DB sources mark coverage partial; free-name hints never promote entitlement.
**Offline method:** run `python3 -m pytest -q tests/test_free_endpoint_burn_dashboard.py` on an isolated working branch with synthetic SQLite fixtures; generate the dashboard from existing read-only OpenCode databases and provider JSON.
**Acceptance:** same event-token totals over repeated read-only runs with stable source DBs; exact coverage and known-missing states; no provider generation/API calls; outputs remain private; no production route, lease, price, admission or timer changes.
**Next authorized stage:** install a reviewed read-only systemd timer after release, archive timestamped JSON snapshots with retention/audit manifests, expose HTML via authenticated fleet UI, and add 429/503, age, plan-variance and evidence-gap alerts. Do not install a timer or expose raw dashboards externally until the operator approves the runtime scope. Never treat observations as permission to consume free tiers.
