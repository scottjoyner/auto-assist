# Router admin transport and cross-service dependency — 2026-10-11

**Release status: HOLD.** This is a value-free read-only operational witness, not
credential-rotation authorization or permission to deploy the router.

## Actual running container observations

| Consumer | Direct Docker-name probe | Configured base URL | Anonymous /admin/runtime-projection | Existing admin token |
| --- | --- | --- | --- | --- |
| AssistX API | Works | Reachable | HTTP 401 | HTTP 200 |
| AssistX worker | Not resolvable/reachable via that Docker name | Reachable | HTTP 401 | HTTP 200 |
| Hermes adapter | Not resolvable/reachable via that Docker name | Reachable | HTTP 401 | HTTP 200 |

The router, API, worker and adapter have the *same-byte* configured
`AUTO_ROUTER_ADMIN_TOKEN`. This check did not print, export, or save its
value or hash. All three clients' configured `AUTO_ROUTER_BASE_URL` values
use **HTTP** and resolved from their own container network namespaces to
**RFC1918** addresses. This verifies existing connectivity, *not* TLS
confidentiality, endpoint identity, or public network isolation.

The worker and adapter are not on `git_default`, while the router and
API are. That does **not** prove a runtime outage, because worker and adapter
use the independently reachable configured base URL. **Do not attach more
Docker networks solely to fix the unsuccessful hostname probe.**

## Credential custody / rotation distinction

The owner-private historical Git matcher currently reports **14 distinct
credential-named live matches** across API, worker and Hermes adapter. The
running router's administrator token is separately observed in all four
containers; it does **not** equal the pinned historical backup's
`AUTO_ROUTER_ADMIN_TOKEN` value. This narrowly means it was not an exact
match to that one historical field in that snapshot; it is **not** proof
the admin token was never exposed or does not need rotation.

The router auth implementation accepts a **single shared admin token**;
there is no proven dual-token grace period. Rotating it without planning
all router and consumer transitions can strand legitimate clients. Owner
must either arrange an approved maintenance window or separately develop
and accept a safely bounded overlapping-key migration.

1. Assign an explicit security owner for the four-service shared admin token,
   separate from the 14-entry historical snapshot manifest.
2. Review use of plain HTTP for admin credentials on the private network.
   Independently validate host/TLS identity or document an approved network
   segmentation + transport-risk exception; private IP alone does not
   authenticate the peer or encrypt credentials.
3. Build a consumer-by-consumer credential transition and rollback plan
   before changing any token. Do not copy tokens into issues, CI, screenshots,
   diagnostic logs, or proposed Compose files.
4. Repeat **GET-only**, no-body 401/authorized 200 checks using each service's
   configured base URL. Do not substitute unconfigured `git-router-1`
   hostname checks or interpret a valid token as signed runtime projection.
5. Coordinate client-first AssistX PRs #288/#290 with Auto-Router #41
   immutable image and supervised production cutover; require remote
   Destroyer and Lenovo post-cutover anonymous 401 receipts.
6. Keep `auto-assist` issue #156 and `auto-router` issue #40 OPEN until
   owners verify key revocation, service continuity, history/cache response,
   and independently observed production behavior.

## Portable review-only tool

`scripts/router_admin_transport_custody.py` inspects only scheme and token
*presence* from the process environment. It neither resolves DNS nor
opens sockets and prints no URL, hostname, resolved address, token, hash,
header, or response body. Current live services return
`HOLD_TRANSPORT_REVIEW`, `HTTP`, with
`PLAINTEXT_SHARED_ADMIN_CREDENTIAL_NEEDS_OWNER_EXCEPTION`.

A future `HTTPS` outcome also remains HOLD until independent peer
identity and credential-custody validation. This CLI deliberately never
returns a release authorization.

**No running service, firewall, Docker network, credential, or provider
configuration was changed to produce these observations.**
