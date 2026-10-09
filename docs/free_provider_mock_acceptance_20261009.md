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
- 126/126 offline pytest cases pass on x1-370 and xwing, asserting zero mock provider
  invocations before admission on all negative controls. For simulated
  in-flight stream failure, exactly the pre-loss mock steps can execute;
  no later steps execute after lost authority or lease expiry.
- Route qualification additionally requires a separate synthetic trusted
  authority to witness the requested and resolved model, account, quota
  group, proof reference, zero-cost price fields and epoch. Self-declared
  extra quota pools or altered $0 evidence are denied before lease request.
- Per-group cooldown and quarantine prevents a second Kilo/OpenRouter
  alias mapped to the SAME upstream pool from bypassing a 429/503.
- Bounded mock streaming (1-16 steps, synthetic clock increments) has
  renewed lease and witness checks before each subsequent step; simulated
  revocation, expiry or partition cancels remaining mock work.
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
   Local group cooldown state is NOT shared fleet-wide and depends on a
   working central trip circuit; the mock quarantine only proves local denial.
4. Knowledge governance FREE-PROVIDER-LEASE-NEXT-CHECKPOINT.md remains a
   production HOLD pending operator scope acceptance, credential custody,
   REAL async cancellation, cross-node loss/replay tests, and trace archiving.
   Bounded synchronous mock steps are not proof of real stream preemption.
5. No changes to existing auto-assist PR #119 lease issuer, PR #121 frontend,
   production services, NAS, credentials, router or provider clients.

Next accepted slice: an independently reviewed authenticated mock transport
with cross-node lease-loss and cooldown tests; require actual zero-dollar
receipts and upstream quota ownership before any real provider activation.

## Two-node shared-lease custody (2026-10-09)

See docs/free_provider_two_node_ssh_acceptance_20261009.md. Two physical
origin nodes contended through existing SSH transport to one disposable
x1-370 SQLite authority. One admitted, one denied, no overlap. After
release, xwing reacquired; zero leases and local audit OK. A remote
copied-state experiment was blocked before execution, so split-brain
safety is NOT claimed. The mock adapter now refuses success on missing
or malformed final release receipts and quarantines the quota group,
bringing isolated tests to 126/126.

## Strict parsing and uncertain-admission hardening (2026-10-09)

The mock-only adapter now rejects truthy but non-boolean verification,
authentication, first-witness, grant and renewal values. It validates
finite lease-expiry timestamps, prevents NaN/Inf or pathological huge integer
expiries from being accepted as authority, and bounds numeric request
reservations and identity/request-key lengths. Malformed positive grant
responses or a dropped authority connection after a possible commit
quarantine the synthetic shared upstream group; a normal slot/limit denial
does not quarantine and can be retried after conditions change.

Observed: 126/126 targeted isolated tests PASS independently on x1-370
and physical xwing; py_compile PASS; no real provider generation or token
usage. This is an offline simulation and no trusted remote issuer,
application authentication, independent quota provenance or physical
stream preemption has been established.
