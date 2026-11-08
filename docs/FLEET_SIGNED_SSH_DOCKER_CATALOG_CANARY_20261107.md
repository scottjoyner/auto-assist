# Authenticated Docker-private → physical-LAN SSH catalog witness

**2026-11-07 ~19:40 EST** — A genuine, bounded one-shot physical test on
`x1-370` crossed the previously unproven boundary from the **running
`assistx-api` container** (`172.21.0.6`) to a transient user-owned HTTP
listener **bound only to a verified Docker bridge** (`172.21.0.1:19201`), then
through strict host-key-authenticated SSH over the *physical LAN* to
`xwing`/`Joyner` remote loopback `127.0.0.1:1234`.

This demonstrates one possible future production transport *without*
opening unauthenticated LAN model ports and *without copying SSH credentials
into the API container*. **It is not deployed, and is not an authorized
production inference gateway or Task execution route.**

## Real physical results (from the API container)

| Test | Expected | Observed |
|---|---|---|
| Matching HMAC from unapproved host-source address | deny | HTTP 403 |
| Tampered HMAC from expected container | deny | HTTP 403 |
| Signed xwing catalog | exact loaded model | HTTP 200, 17 model IDs, exact expected ID |
| Replayed signed nonce | deny | HTTP 403 |
| Signed Joyner catalog | exact loaded model | HTTP 200, 1 model ID, exact expected ID |
| Signed unapproved node | deny | HTTP 403 |
| Stop transient private server | close listener | PASS; TCP `172.21.0.1:19201` no longer listening |

The end-to-end local test exited PASS, but the server returns **exit 2
(HOLD)** if its bounded request/time loop reaches normal completion. Its output and
model catalogs never contain a Task authorization or model-generated text.
Both production API and Neo4j were healthy before/after. No UFW, SSH trust,
model bind, provider, Task, approval, Neo4j or credentials were changed.

## Non-admitting design and constraints

New script: `scripts/fleet_signed_ssh_docker_catalog_canary.py`.
The default invocation **does nothing** except report HOLD. A live canary
requires explicit `--live`, Docker-private listen/client IPv4 addresses,
short timeout/size/connection limits, and a 32-byte **ephemeral HMAC key
passed only on an inherited file descriptor**. Secret material is not accepted
via command-line arguments, environment or persistent files. The caller
must deliver the same short-lived key through a separate, trusted operator
transport to the *approved container process* (in the physical test, ephemeral
container stdin, not environment or command-line).

Before it binds, the server checks that the listener is a real `br-...` or
`docker0` interface and the allowed container IP is within its valid IPv4
subnet. Requests are restricted to that source and `POST /canary`, with
strict JSON `{"node":"xwing"}` or `{"node":"joyner"}` only, exact
Content-Length and Content-Type, 64-byte payload maximum, expiry-restricted
HMAC-SHA256, nonce replay rejection, constant-time comparison, 7-request
maximum and a 30-second time ceiling. `GET` and `PUT` deny access. The
host-side SSH client is the reviewed fixed-endpoint read-only script
`fleet_ssh_lan_model_readonly_witness.py`; it uses fixed numeric physical
LAN IPs, expected known-host identities, no SSH proxy, no password/agent
forwarding, `ssh -W` to remote loopback, strict model ID and 64 KiB response
validation. No permanent forwarding listener is created.

**Zero assigned slots.** Responses are categorical and always report
`task_admission=HOLD`, `direct_lan_http_verified=false`,
`production_ingress_authorized=false` and `approved_parallel_slots=0`.
No model generation endpoint is forwarded; the only remote request is
`GET /v1/models`. Do not treat source-IP + ephemeral HMAC as a complete
production service identity or owner-signed runtime approval.

## Verification

The synthetic offline regression suite includes expired/tampered signatures,
nonce replay, wrong/unknown node, malicious Docker interface metadata,
unknown source, duplicate JSON fields, bad HTTP envelopes, a real local HTTP
HMAC test and default no-network handling. GitHub's existing dedicated
SSH-LAN workflow runs **tests only**, with token `contents: read`; it never
executes `--live`, requests SSH keys or contacts any fleet endpoint.

Reproduction of the **physical** staging test requires an operator with the
existing SSH host-key trust on `x1-370`, an active and authenticated API
container on the same verified Docker bridge, and a short-lived secret-FD
launcher. It is not suitable for cron or unattended production execution.

## Remaining release gates

1. Dedicated, least-privilege SSH principal and credential lifecycle for any
   persistent gateway, with identity binding, rotation and audit trail
2. Owner-reviewed, source-restricted access and private authenticated model
   routing policy; explicit code and inference limits; independent failover,
   timeout, backpressure, connection teardown and incident rollback tests
3. Resource-owned slot accounting and fenced claim/COMMIT in the worker path
4. Fresh, independently authorized signed canonical projection (generation
   **896 is expired** at physical observation), with runtime, artifact,
   access-path and capacity provenance; WorkSupply is correctly at **zero
   approved slots**
5. One supervised positive machine Task only **after** all gates pass

Do **not** re-enable the old projection keepalive or create a new generation
using its stale hard-coded October approval ID. See #284, #501 and #569.
