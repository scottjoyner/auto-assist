# Dynamic Tailnet model observation — read-only acceptance gate

Date: 2026-10-10. Owner: AssistX Runtime Directory. This is an **observation**
channel, not a shortcut around signed runtime admission or model-handle routing.

## Existing interfaces reused

- `scripts/reconciliation-discover-tailnet.py` gathers current Tailscale
  membership into a candidate-only inventory with custody checksum.
- `scripts/reconciliation-probe-tailnet-models.py` takes that snapshot and
  examines only online peers with observed Tailnet IPv4 in 100.64.0.0/10,
  only on 1234–1236 or explicit per-node operator-approved ports.
- `src/assistx/mobile_observation.py` sanitizes fresh results for mobile.
- `GET /api/v1/runtime/observations` requires the existing authenticated
  mobile Tailnet boundary. It deliberately does not set admission status.

## Model states

`installed_not_loaded`: LM Studio native inventory reports no loaded instances.
`advertised_unverified`: OpenAI-compatible `/v1/models` reports a model but
does not prove memory residency. `resident_verified`: native loaded instances
or exact llama.cpp `/health` plus `/props` evidence. None proves that
completion works, correct permissions exist, or the signed projection admits it.

The mobile read model has no node, Tailscale IP, port, URL, model file path,
artifact fingerprint, executor token, admission handle or selectable route.
All observed models are explicitly `admitted: false`, `selectable: false`.

## Private one-shot operator run

```bash
set -euo pipefail
run_dir="$(mktemp -d /tmp/assistx-fleet-observation-XXXXXXXX)"
chmod 700 "$run_dir"
python3 scripts/reconciliation-discover-tailnet.py --output "$run_dir/peers.json"
python3 scripts/reconciliation-probe-tailnet-models.py \
  --input "$run_dir/peers.json" \
  --output "$run_dir/model-witness.json" \
  --ports 1234,1235,1236 --workers 6 --timeout 0.8
sha256sum -c "$run_dir/model-witness.json.sha256"
```

A separate, **not installed** candidate refresher is
`scripts/reconciliation-refresh-tailnet-models.sh`. Invoke it with
`--output /private/mode-0700/witness.json`, optionally `--input` to run an
offline fixture without probing live peers. It rejects nonprivate output
directories, refuses symlink outputs, uses a single `flock` lock, retains the
previous witness if another run is active, and verifies the atomic checksum.
Before actual deployment the operator must approve a narrowly scoped user
timer (approximately once per minute to satisfy the 120-second TTL), the
volume mount and its permission model. No timer or bind mount is installed.

The private evidence JSON retains per-endpoint GET path/status/byte counts
and SHA-256 lineage to the **exact candidate inventory bytes**. Both the witness
and receipt are atomically replaced and created with `0600` permissions. Do not
commit raw observations or private Tailscale coordinates to a public repository.

## Staleness and deployment boundary

The sanitized endpoint reads `ASSISTX_TAILNET_MODEL_OBSERVATION_FILE`
only when configured; absent or invalid files return no models. A report older
than 120 seconds is `stale` and exposes **no model list**. A future timestamp
over 30 seconds is also stale. Setup requires an operator-owned file mounted
read-only into the AssistX API container and a separately approved periodic
collector unit. Neither is installed by this PR.

Tailnet Serve must route the authenticated `/api/v1/runtime/observations`
path to AssistX before mobile can consume it. Authenticate via the existing
Tailnet header path; do not make the observation endpoint public or use
a blanket rewrite rule that bypasses upstream identity.

## Live evidence — read-only witness run

Ran on x1-370 on 2026-10-10. Tailscale inventory yielded 12 eligible
online peers and 36 bounded endpoint targets. 12 endpoints had resident-model
evidence; 23 were unreachable and 1 had native installed-but-unloaded models.
Examples of nodes with responsive inference endpoints were Beelink,
Deathstar, Destroyer, Joyner, Lenovo, OptiPlex and x1-370. Not all verified
models are chat models: embeddings are present. No chat canary was attempted,
and no execution/authority/projection mutation was performed.

Custody (private x1-370 session): witness SHA-256
`9971964821620cceeace3fa4956d430eb3c2749adb10be4c5f8e2ea0da7a5e52`.
The exact receipt is local in the restricted scratch directory; the SHA
covers the previous observation script version before additional model-kind
hardening. A subsequent hardened operator run on 2026-10-10 observed 13
eligible nodes, 39 endpoints (11 resident-evidenced, 26 unreachable, two
installed-but-not-loaded), with the exact source-inventory checksum verified
and `0600` witness/receipt permissions. Hardened private witness checksum:
`29db19ff7452bab86c4881d1445578f08ac330bcf02501343934c95f15242986`.
Its custody is `/tmp/assistx-discovery-atomic-20261010-YqOJ1Jpr/` on x1-370.
Temporary scratch is not a durable retention promise. A new witness after
review/merge is still required for release-grade acceptance.

## Remaining acceptance gates

1. Validate correct service classification for LM Studio native, llama.cpp,
   proxy/OpenAI compatible endpoints, model aliases and embedding services.
2. Support per-node service announcements/agent heartbeats for nonstandard
   ports, with trusted identity, TTL, and negative/revocation evidence.
3. Require a separate signed admission projection to issue selectable handles;
   observational models must never automatically flow into the router.
4. Reconcile the expired projection approval; do not rotate/re-sign it from
   this feature. Fix the independent error-handling regression in AssistX #254.
5. Physical iOS picker acceptance: load the sanitized model list but show
   observed non-admitted models disabled and clearly distinguished from
   authoritative Fleet Models and Agent Auto.