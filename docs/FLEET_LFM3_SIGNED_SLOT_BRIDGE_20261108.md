# LFM3: authenticated container → host-owned slot → bounded inference

**2026-11-08, x1-370. Research/staging only. Production Task admission: HOLD.**

## Why this is different from the earlier proofs

Previously, #567 proved a fixed signed Docker-private LFM3 model canary,
#574 proved authenticated Docker→SSH-LAN *catalog* reads, #575 required a
separately signed runtime owner approval, #576 rejected stale WorkSupply at
Task claim/effect/commit, and #577 merged a host-local SQLite cross-process
slot lease primitive. **The missing link** was a real physical Docker-private
request that **acquires and fences the host-side slot before the model
invocation**, and refuses to return a positive result on revocation.

This patch is an **operator-only one-shot bridge** that combines those
components without creating any production grant or starting a daemon.

## Exact staging architecture

A live `assistx-api` container at `172.21.0.6` sends an HMAC-SHA256 signed
`POST /staging-canary` to a short-lived host listener on the **verified Docker
bridge** `172.21.0.1:19203`. The listener accepts only that exact source IP,
method, path, ≤512-byte fixed benign JSON body, canonical shape, one content
length/type and one of each signing header. The HMAC covers exact method,
`/staging-canary` path, current 10-second timestamp window, 32-hex nonce and
SHA-256 of the body. Nonces cannot be reused in that process, including after
an unsuccessful physical inference. Its domain differs from the old
`POST /canary` signature; a signature issued for one is not accepted by the
other. Unapproved source, malformed header, wrong model/body, stale/tampered
signature, and replay are denied **before any slot reservation/model call**.

The listener takes a **previously provisioned, private synthetic-only**
SQLite `resource_grant` matching exactly `lfm3-x1-staging-only`, synthetic
generation `897`, one slot and all-zero synthetic receipt marker (neither a
real Ed25519 signature nor a production lease). It does not create that DB.
It atomically acquires the one slot through the #577 ledger, tests the
resource-side fence, and calls only the existing verified resident LFM3
model at `127.0.0.1:19181`. The prompt is fixed `Reply OK.`, with maximum
four completion tokens. Client inputs cannot choose another model, endpoint,
prompt, tool, completion limit or Task.

**The upstream client runs as a separate spawn-mode child process** with an
8-second hard deadline, shorter than its 11-second slot lease. On timeout,
its HTTP client process is terminated/killed before the lease is released.
A still-running upstream server may continue work after client disconnect;
this is **not** a proof of process-level cancellation. The host checks the
same Task/claim/epoch/secret/expiry before and after the bounded request, and
**must successfully release the slot before returning HTTP 200**. Invalid
response shape, expiry, revocation during the model request, or lost release
CAS returns HTTP 423/HOLD; no model-generated text, grant token, owner
signature, raw model catalog or secret is returned. Every response explicitly
says `task_admission=HOLD`, `approved_parallel_slots=0` and
`production_inference_authorized=false`.

The HMAC secret is ephemeral 32-byte randomness delivered to the host bridge
via an inherited FD and to the existing authenticated API container's
one-shot client via stdin, never logged, stored or sent as CLI arguments or
an environment variable. No SSH credentials are needed for this local model.
The gateway runs as non-root, has a maximum 4 requests and 20-second lifetime
(14 seconds in the physical harness), and **always exits code 2/HOLD**.
It closes the listener and does not persist data or a service.

## Genuine physical evidence, November 8

A deliberate read-only-client staging execution using
`fleet_lfm3_slot_bridge_operator_probe.py --live --listen-ip 172.21.0.1
--client-ip 172.21.0.6` passed this observed matrix:

| Verification | Real physical outcome |
|---|---|
| Correct HMAC from unapproved host source | 403 deny |
| Wrong HMAC from existing `assistx-api` container | 403 deny |
| Valid signed container-to-host fixed LFM3 request | HTTP 200; **4 completion tokens** |
| Replay the exact valid HMAC nonce | 403 deny |
| Host slot held and revalidated across bounded inference | PASS, including release before positive reply |
| Ephemeral ledger removal | PASS, no fixture left |
| Listener teardown | PASS, port 19203 no longer listening |
| Resident LFM3 and AssistX API health | Both healthy after test |
| Production Task authorization | **HOLD, 0 slots** |

The output deliberately reports `physical_stage=PASS` and separately
`task_admission=HOLD` and `owner_signed=false`. The staging probe's output
contains only numeric evidence/denials, not model text or credentials. The
`--live` harness is *not* invoked from CI. Running either executable with no
live/staging flag performs no model, graph or database work and exits 2.

## Tests and exact release boundaries

`tests/test_fleet_lfm3_signed_slot_bridge_canary.py` tests real local HTTP
with synthetic HMAC and a fake, fenced resource ledger: wrong caller, route,
method, model body, duplicate headers, oversized content length, bad signature,
stale timestamp, replay, slot exhaustion, grant loss during inference and
**after final validation but before release**, no positive result when any
step fails, and hard child-deadline termination. Dedicated CI runs these
negative tests, #577 host-local CAS tests and earlier #567 fixed-canary
contract tests **without live networking or production secrets**.

**This does not make model-host inference exclusive.** Today the existing
`llama-server` is reachable directly on `127.0.0.1:19181` by other host
processes, outside this ephemeral bridge. A staging synthetic all-zero receipt
can be created by a local user; that is expressly **not** sufficient for
production. There is no durable gateway service identity, independent owner
Ed25519 public verification root on this node, authorized fresh 897
canonical generation, credential custody/rotation, supervised rollout,
router-side cancellation/COMMIT or guaranteed exclusive model process socket.

Before production Task execution, the operator must independently authorize
and provision a distinct least-privilege host-side gateway principal and
**exclusive** upstream model transport (including blocking all bypass routes);
verify and bind the external owner-signed runtime/model/roster/capacity grant
with a privately owned ledger; enforce fully authenticated model access,
resource-owned per-request leases, in-flight abort on lease loss, signed
claim/COMMIT fencing, crash/expiry/replay tests, and controlled rollback;
only then issue a fresh owner-signed canonical generation and supervise one
Task canary. The old keepalive must remain disabled. See issues #284, #569,
#501 and #577.

Production API and Neo4j were not restarted or updated. No production task
claim, graph write, credential, UFW rule, deployment or admission setting was
changed as part of this evidence collection.
