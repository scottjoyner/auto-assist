# Free-provider mock admission: offline acceptance (2026-10-09)

Owner: knowledge issue #57; parent #55.
State: RESEARCH / MOCK ONLY. Not authority to open live routes,
change real credentials, enable dispatch, or merge into a release.

## Prediction

Multiple free-model IDs and credentials do not imply separate token pools.
A fail-closed adapter can reuse the October 7 SQLite pilot's
acquire / renew / release / trip contract without any real provider calls.
Requests without exact-model and resolved-upstream identity, account scope,
zero-cost price proof, trusted lease epoch, node-bound principal, or a fresh
lease witness must be denied. Router aliases are not independent quota proof.

## Implementation and observations

- scripts/mock_free_provider_admission.py accepts ONLY a
  RecordingMockProvider. No HTTP client, provider requests, secret reads,
  production integration, or live routing exists.
- Synthetic route qualifications carry provider ID, requested and resolved
  model IDs, quota group, account scope, provenance ID and prices. These are
  fixtures, not attested provider cost receipts.
- A separate synthetic identity service checks client, node, account, lease,
  request key and epoch before and after lease renewal. Unknown provider,
  client, key, node, epoch, missing/expired lease and outage deny.
- 401/402/403/429/503 responses cause shared-circuit reporting and local
  cooldown. Unavailable reporting causes quarantine. Missing or nonzero
  usage receipts quarantine as well. No paid fallback or blind retry.
- 45/45 offline pytest cases pass on x1-370, asserting zero mock provider
  invocations on negative admissions.
- Existing SQLite pilot from commit 648683e4 was separately loaded to
  ephemeral /tmp under a temporary SQLite database with one slot and one
  request/window. Two synthetic routes shared one quota group: first
  admitted, second denied; 1+0 mock calls; audit verified true; zero
  remaining active leases. No real provider generation calls took place.

## Limits and further gates

1. The pilot is single-host only. No split-brain safety, remote authentication,
   signed responses, independent authority witness or off-host durable audit
   was established.
2. Mock principal and price evidence are synthetic assertions, not real
   account entitlement or independent usage receipts.
3. Live OpenCode registry entries for Cohere, Z.AI, Kilo, OpenRouter,
   OpenCode and Cerebras do not establish distinct physical quota groups.
4. Knowledge governance FREE-PROVIDER-LEASE-NEXT-CHECKPOINT.md remains a
   production HOLD pending operator scope acceptance, credential custody,
   async cancellation, cross-node loss/replay tests, and trace archiving.
5. No changes to existing auto-assist PR #119 lease issuer, PR #121 frontend,
   production services, NAS, credentials, router or provider clients.

Next accepted slice: an independently reviewed authenticated mock transport
with cross-node lease-loss and cooldown tests; require actual zero-dollar
receipts and upstream quota ownership before any real provider activation.