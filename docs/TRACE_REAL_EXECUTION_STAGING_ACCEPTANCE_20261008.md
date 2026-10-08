# Real AssistX trace execution: two-node staging acceptance (2026-10-08)

**Status:** source-integrated, **not production deployed**. Operator approved bounded real
execution on 2026-10-08. This approval does not waive node authentication,
revocation, audit or readiness requirements.

## Prediction / prospective release gate

The prediction for the **next production-gated test** is: one genuine
AssistX/Neo4j `CLAIMED` task per authorized physical node, typed
`kind=ticket_type=trace_probe`, payload `probe.noop.v1`, will receive a
short Ed25519-signed lease and an independent current-state proof bound to
the node's freshly generated nonce. The pinned public key must verify both
proofs. Exactly two local fsynced journal records per accepted task will
survive an independent encrypted NAS restore; cancelled, expired,
superseded, wrong-node, invalid-key and network-outage cases must produce
**zero execution records**. The issuer must be reachable over protected
transport and unavailable otherwise; a signature alone is not enough.

**Important provenance:** the prediction above is a prospective acceptance
criterion for deployment, recorded *after* the local, staging-transaction and
physical fixture experiments below. It is not a claim those historical tests
were pre-registered.

## Implementation

- `src/assistx/trace_claim_live_executor.py`: isolated dual signed-proof
  consumer and `probe.noop.v1` only, with explicit real-execution opt-in,
  known physical nodes, registered token, a pinned owner-controlled
  issuer Ed25519 public key, owner-private journal, HTTPS issuer URL
  (loopback-only exception), nonce and strict task-claim identity.
- `src/assistx/fleet_node_agent.py`: advertises `trace-probe` only if all
  real-execution preflight checks pass; a manual capability override cannot
  bypass preflight. Checks target before claim, uses node token for
  claim/heartbeat/completion, calls dual-proof consumer before local no-op.
  Ordinary non-trace task handling remains on the existing path.
- `tests/test_trace_claim_live_executor.py`: normal proof pair, replay,
  wrong signer/challenge, issuer/status outage, flag disabled, bad
  key/identity, mutated claim, HTTPS boundary, full worker lifecycle.
- `scripts/trace_claim_lease_staging_canary.py`: real Neo4j transactions
  plus actual FastAPI route factory and worker code, using fixture node
  authentication and temporary in-process key, then mandatory rollback.
- `scripts/trace_claim_live_platform_smoke.py`: offline fixture-issued
  proofs on **existing** physical shadow releases and local fsynced journals;
  never executes an OS command or calls the actual AssistX issuer.
- `.github/workflows/trace-execution-acceptance.yml`: separate regression
  gate; full repository-wide CI remains independently required.

## Observations (not forecasts)

**Staging, current Neo4j service:** four cases passed: xwing success (2
receipts), xwing cancel before current-status (0 receipts), MacBook Air
success (2), MacBook Air cancel (0). The real Neo4j Task transactions
were rolled back, with **zero persisted fixture rows**. Actual FastAPI
router and real worker were exercised via an isolated process/container
test harness, but authentication and transport were fixture substitutes.
This was **not** deployed AssistX API execution or remote node work.

**Physical shadow, offline fixture transport:** current dual-proof
consumer executed one locally signed no-op on each authorized physical
host, retaining all prior journal entries:

| Node | Before | After | Latest trace SHA-256 |
| --- | ---: | ---: | --- |
| xwing | 10 | 12 | `4f4dc3fa95b9f04d134df76f72f715888166ce33678101180d2539855a1626c1` |
| scotts-macbook-air | 8 | 10 | `e65e592164a9696f176acee8e8f5ac3f453c9c24df0af557a1bcd926e3d04614` |

An initial xwing test failed closed because the shadow directory still had
an older `trace_claim_lease.py` without the canonical `kind/ticket_type`
validator. Staging was corrected **in the shadow release only** before
either successful run; no live process or service was restarted.

**Encrypted NAS custody:** `gpg-aes256-symmetric` backup and independent
decrypt/restore verification both passed after the physical runs:
**4 distinct generations verified per node**, latest records 12 and 10,
respectively. The signed archive heads were
`1867ddb38ab48e539ac3c769c44ad5db1a86b7321111be1badbcf75224fc5823`
and
`b060cf580b8d4d0ab854a31b9614e7452245c14ab18e9002700028f00be620be`.
Existing earlier generations and node journals were preserved.

**Tests:** prior branch had 96 focused tests. This slice's expanded
trace/recovery/concurrency set passed **118** locally before
the physical smoke addition. The physical scripts compile/lint and ran
on Linux and macOS. The separate CI workflow has not yet been observed
green on GitHub; do not substitute this local result for CI evidence.

## Release and rollback hold

1. Deploy an **isolated** staging issuer process with its own private key,
   escrow/rotation, target/node identity secrets, TLS/Tailscale ingress and
   read-only access to actual current AssistX claims. Do not use the offline
   shadow-fixture signers as production issuer authority.
2. Register `xwing` and `scotts-macbook-air` node-specific tokens and pin
   only the dedicated issuer public key on those owners' physical machines.
   Keep private signing material controller-only; do not store it in Git,
   NAS plaintext or node shadow releases.
3. Verify the actual deployed API serves the two lease/current-status
   routes and that one genuine committed AssistX-issued claim per node
   can complete with the live signed handshake and ciphertext archival.
   Require cancelled/superseded/outage negative tests, correct hash chain,
   0 unauthorized tasks, and monotonic revocation fencing before expanding
   beyond fixed no-op.
4. Stage rollout one node at a time with explicitly scoped environment
   flags. Both `FLEET_TRACE_PROBE_ENABLED` and
   `FLEET_TRACE_REAL_EXECUTION_ENABLED` default false and must be true
   simultaneously for this one allowed `probe.noop.v1`. Keep
   `FLEET_UNSAFE_SHELL_TASKS_ENABLED=false`; do not widen the allowlist.
5. Verify broad CI against a base-branch failure baseline. Existing CI
   run 37787026923 reported 749 passed / 77 failed and a failed recovery
   canary; `RepositorySourceBinding.from_contract_payload` was shown to
   be missing from the base as well as the PR. A dedicated trace CI
   acceptance job is additive, not a waiver of these failures.
6. Roll back by disabling either real-execution flag or issuer route,
   stopping staged node admission, leaving all archive and journal evidence
   intact. No general shell executor or free-provider routing change is
   authorized by this proof-of-no-op.

## Reproduction

```bash
cd /home/scott/git/wt-assistx-trace-pr-20261008
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_claim_live_executor.py tests/test_trace_claim_lease.py \
  tests/test_trace_shadow_grant.py tests/test_trace_execution_shadow_backup.py \
  tests/test_trace_execution_adapter.py tests/test_trace_execution_shadow_paths.py \
  tests/test_fleet_node_shell_gate.py tests/test_fleet_node_recovery.py \
  tests/test_safe_fleet_executor.py tests/test_fleet_executor_concurrency.py
```

The Neo4j canary requires its explicitly authenticated staging/container
connection environment. It uses `begin_transaction`, always calls
`rollback` and independently verifies that no test rows remain.
Do not run it under a broad uncontrolled orchestration schedule.

### macOS Python 3.9 compatibility acceptance

A pre-promotion Mac check found that `datetime.UTC` is unavailable in its
Python **3.9.6** runtime. The worker now uses `datetime.timezone.utc` and
keeps all optional `cryptography` imports *behind both explicit real-execution
flags*, including during task polling. The corrected full worker module was
staged under its existing **shadow-only** release tree and imported successfully
on the actual Mac; `_detect_capabilities(None)` did **not** advertise
`trace-probe` when the production flags were unset. No Mac production
worker was restarted or replaced. This replaces an earlier version that
could break lightweight nodes merely by importing optional crypto.

### CI compatibility correction (follow-up)

The first GitHub run of the dedicated `trace-execution-acceptance` job
**passed**. The same PR's broad `ci` job stopped at Ruff `UP017`, which
tries to replace `timezone.utc` with `datetime.UTC` even though the actual
Mac Python 3.9 runtime does not support `datetime.UTC`. The worker now
has a single-line `# noqa: UP017` compatibility exception, preserving
repo-wide lint policy. This exception passed the exact blocking CI Ruff
rule selection locally. The broad CI test-phase baseline remains a separate
release hold until its next run and comparison with main.


## Additional pre-production hardening (October 8)

**Prospective prediction for the next isolated issuer pilot:** Explicitly
pinning `FLEET_TRACE_ISSUER_ORIGIN` on each physical node and validating it
before the first claim request will deny cross-origin and malformed issuer
configuration without transmitting the node token. Refusing admission if the
local hash-chain journal is corrupt, local filesystem free space is below
128 MiB, or the journal reaches 7 MiB (against the existing 8 MiB archive
snapshot limit) will prevent additional unauditable work without deleting
historical receipts. These assertions were specified and tested during
this enhancement pass, not before earlier shadow experiments.

**Changes:**
- `trace_claim_live_executor.py` validates an explicit issuer origin; HTTP
  is accepted only for loopback, HTTPS otherwise, with no userinfo,
  query/fragment or URL path. `fleet_node_agent.py` enforces this origin
  match **before its claim request**. Preflight refuses a missing pinned
  issuer origin.
- A present journal undergoes chain/ownership verification before trace
  capability admission. At >=7 MiB, further trace work is denied pending
  archive segmentation; the journal is never shortened. `statvfs` must
  report at least 128 MiB free locally.
- Explicit negative tests cover missing/incorrect/malformed issuer origins,
  failed audit chain validation, near-archive-limit retention (byte size
  unchanged), and a low-free-space filesystem fixture.
- **127 trace/recovery/concurrency tests passed locally** after these
  changes. The four-case real-Neo4j rollback-only FastAPI+worker canary
  passed again with zero committed fixture rows. Read-only physical
  preflight passed on both xwing and the MacBook Air, with prior journal
  counts unchanged at 12 and 10 respectively.

**Still blocked:** The worker's shared urllib HTTP transport can follow
server-issued redirects. Even with a configured origin pin, that transport
does not yet fail closed on redirects; this must be independently fixed and
tested on trace claim, heartbeat, status-proof, and completion requests
before trusting live node credentials. The existing monolithic snapshot
writer cannot safely archive beyond 8 MiB; the 7 MiB stop gate is temporary
backpressure, **not** full long-term retention/offload or a reason to trim
history. Do not deploy a real issuer or promote unrestricted execution until
these and the prior key/CI/cancellation gates are accepted.


## Redirect-resistant authenticated trace transport

**2026-10-08 acceptance under issue #126:** Trace-capable node polling and
credential-bearing claim, heartbeat, lease, status and completion requests
now use a nonredirecting urllib opener. All HTTP 3xx responses fail closed:
the worker does not contact redirect targets. The ordinary non-trace HTTP
path retains its legacy behavior, avoiding unrelated worker disruption.

**Prediction:** A secondary local HTTP listener must receive *zero*
requests, including when the primary listener responds 301/302/303/307/308
to a fixture-authenticated POST. Relative same-origin redirection must
not be followed. The credentialed trace-capable GET poll must also refuse
redirects before fetching work. Non-trace urllib behavior should remain
unchanged.

**Observation:** `tests/test_trace_http_redirect.py` runs two real
loopback HTTP listeners with fixture-only credentials and verifies zero
second-listener requests. Redirect tests cover claim, heartbeat, lease
proof, current-status, completion and task polling, plus no-redirect
success. Existing tests and new redirect tests: **160 passed** locally.
No real node credentials, persistent claims, production issuer or shell
execution were involved. The suite is now part of dedicated trace CI.

**Limit:** TLS certificate verification, live token registration,
independently witnessed revocation and authenticated production issuer
availability still require deployment acceptance. These tests validate
transport redirects, not full runtime production security.
