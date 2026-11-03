# Fleet-node effective credential and capability census — November 3, 2026

## Verified physical diagnosis

Fleet Commander inspected five **active** user-level `fleet-node-agent.service`
instances on xwing, Deathstar XPS-8920, Destroyer, Lenovo 330S and OptiPlex
9030. Their protected `agent.env` credential files were owned by the service
user, regular (not symlinks), and limited to owner-only access. Each live
`/proc/<MainPID>/environ` `FLEET_AUTH_USER` / `FLEET_AUTH_PASS` pair matched the
corresponding file's values by in-process constant-time comparison. Neither
values nor reproducible password fingerprints were printed or persisted.

Using the **effective credentials of each running worker**, private read-only
`GET /api/agent/tasks?status=READY&limit=1` on the already configured
Tailnet AssistX endpoint yielded HTTP **401 on all five hosts**. The
credential-file version gave the same 401. This distinguishes credential
*loading parity* from backend *acceptance* and shows that merely restarting
these five processes cannot repair authorization. The server-side route uses
general `Depends(auth)` Basic auth; the live API process has both the
`BASIC_AUTH_*` and `ASSISTX_*` pairs configured, and these are equal on that
server. This evidence does **not** authorize copying operator credentials
onto nodes or relaxing HTTP Basic auth.

Each of the five live worker source copies still queries the obsolete
`state=READY` rather than `status=READY`, without the new 401 backoff logic.
The separately validated agent source fixes are drafts [#482](https://github.com/scottjoyner/auto-assist/pull/482)
and [#494](https://github.com/scottjoyner/auto-assist/pull/494), not deployed.
AssistX's canonical `WorkSupplyState` remains `allow_background=false` and
`total_slots=0` due expired signed projection; router production remains
degraded with no enabled free hosted APIs.

## Extended preflight semantics

This stacked draft extends the existing [PR #490](https://github.com/scottjoyner/auto-assist/pull/490)
source-only worker-readiness preflight:

1. Read-only systemd MainPID resolution and *same-user*, identified worker
   process `/proc` inspection; no process injection, signaling or restart
2. Constant-time equality of process and secure file credential pairs,
   reporting only `true`, `false` or `NOT_CHECKED`
3. Read-only effective-process Basic probe, reusing the file's HTTP result if
   pairs agree, otherwise querying the configured private Task endpoint once
4. No redirects or inherited proxy; credential-bearing GETs restricted to
   numerical loopback/Tailnet HTTP, numerical private HTTPS, or localhost;
   cloud metadata, public DNS and unexpected endpoints are rejected
5. Bounded JSON read; output **never** includes credentials, raw environment,
   remote Task payloads or reversible secret fingerprints
6. Generic native loaded models, chat models and embedding models are reported
   separately; an embedding model cannot satisfy `expected_model_exactly_loaded`
   for a chat worker

The report always has `authentication_grants_task_admission=false`,
`approved_runtime_slots=NOT_CHECKED`, and `authorizes_dispatch=false`.
Successful Basic authentication, if achieved, cannot prove signed work-supply,
model artifact identity, claim/session/lease authority, or useful Task receipt.

## Minimal operator-controlled recovery

A designated credential owner must reconcile which **scoped machine identity**
may poll/claim work with the deployed API's explicit authorization contract;
this cannot be replaced by mass distribution of Basic operator credentials.
The worker source and exact runtime image must then pass one-node staging
validation, including 401 backoff and `status=READY` query tests. Separately,
renew the canonical runtime, loaded model/artifact/path/capacity evidence
through its designated independent approval authority, and close transactional
claim/session/lease P0 [#428](https://github.com/scottjoyner/auto-assist/issues/428).
Only after a real claim-fenced Task plus independent accepted receipt can
any node's inference count as approved production utilization.

**No credentials, services, Task graph records, leases, signed approvals,
models, firewalls or live routing were changed during this census.**
