# Five-node AssistX fleet agent recovery preflight — Nov 2, 2026

## Verified physical failure

Read-only Fleet Commander inspection found **five active** `fleet-node-agent.service` units, on xwing, Deathstar, Destroyer, Lenovo and OptiPlex. Every service runs the small `~/fleet-work/run_fleet_node_agent.py` wrapper, loading `fleet_node_agent.py` from the same legacy source tree. SHA-256 on every host: `2aef0a3c9cfa51faf6b7d1cfa9bead27720789ce0b25bf315c494022f8cc0f3d`.

That source still uses `state=READY&limit=20` rather than the server's `status=READY` contract and has no auth-denial backoff. Each of the five existing mode-protected per-node `~/.config/fleet/agent.env` files contains nonempty `FLEET_AUTH_USER` and `FLEET_AUTH_PASS` fields, but **the credentials interpreted by this read-only preflight from those files returned HTTP 401** from a read-only GET on the configured private AssistX endpoint. No secret values or response bodies were displayed, logged or modified. Existing server `BASIC_AUTH_USER` and `BASIC_AUTH_PASS` were verified configured, not printed.

Over an 80-second API log sample, `48` `GET /api/agent/tasks` requests returned HTTP 401 (40 with obsolete `state=READY`, eight with `status=READY`), compared with 11 HTTP 200 task polls. NAT bridge source addresses cannot prove the individual host of each denied request; **do not claim source attribution per request**. The five live wrappers and independent credential checks establish the deployed source/auth failures.

## Read-only release preflight

Run `scripts/fleet_node_readiness_preflight.py` **locally on the node** in its normal user session, providing only the existing worker-source path, existing 0600 agent env-file path, configured private AssistX URL and local model URL:

```sh
python3 scripts/fleet_node_readiness_preflight.py \
  --worker-source /path/to/deployed/assistx/fleet_node_agent.py \
  --credential-file "$HOME/.config/fleet/agent.env" \
  --assistx-url http://PRIVATE-ASSISTX-TAILNET:8000 \
  --model-url http://127.0.0.1:1234 \
  --expected-model liquid/lfm2.5-1.2b \
  --expected-sha256 APPROVED-DEPLOYED-WORKER-SHA256
```

The tool performs **GET only** on task listing and model inventories, checks the unit's state and deployed source digest, and prints a JSON report with no credentials or task contents. It refuses symlinked, unowned or group/world-accessible credential files and will not follow HTTP redirects carrying Basic Auth. `/v1/models` catalog count is separate from `/api/v1/models` actual `loaded_instances`. A compatibility-only OpenAI endpoint returns `native_inventory_authoritative=false`, **not** an idle or approved model. When an expected model is supplied, only exact native loaded keys match.

Reported `approved_runtime_slots` is deliberately `NOT_CHECKED` and `authorizes_dispatch=false`. This utility does **not** validate the canonical signed projection, immutable image provenance, GPU ownership, source-owner approval, runtime credentials custody, task admission, completion receipts or counterparty rollback.

## Current node observations (no changes)

| Node | Agent | Auth GET | Catalog :1234 | Native-loaded :1234 |
|---|---|---|---|---|
| xwing | active, stale source | 401 | 17 entries | 2 |
| Deathstar | active, stale source | 401 | not responding | unavailable |
| Destroyer | active, stale source | 401 | not responding | unavailable |
| Lenovo | active, stale source | 401 | 1 entry | native API HTTP 404 |
| OptiPlex | active, stale source | 401 | not responding | unavailable |

Other ports may have healthy local servers, but those are not interchangeable with the configured `:1234` endpoints or signed model identities.

## Ordered production HOLD / recovery handoff

1. Identify the **authorized secret owner** and audit source/custody of node credentials and service auth. Rotate/provision approved values through protected credential management; no credential guessing or anonymous fallback.
2. Validate exact worker release from [PR #482](https://github.com/scottjoyner/auto-assist/pull/482) and apply to a **single explicitly approved node**, verifying source SHA, normal unit wrapper, command and process identity, 401/403 backoff, and GET authorization.
3. Independently establish native loaded model's exact artifact+alias, per-host listener, signer/canonical projection freshness, valid claim/lease/generation and access-path custody. Do not promote a catalog-only or mismatched port.
4. Execute one *real, approved* AssistX task, verify model answer quality and current Task ID/claim+session/terminal acknowledgment, with a separately custodied receipt, before widening concurrency.
5. Retain rollback and prior credentials under owner control; observe failure rate and accepted completions rather than a raw CPU utilization target.

Reference: [AssistX P0 #428](https://github.com/scottjoyner/auto-assist/issues/428) server-side claim fencing remains unresolved. The canonical work-supply state currently reports `allow_background=false` and zero slots due expired projection. Do **not** merge or deploy based on this diagnostic alone.
