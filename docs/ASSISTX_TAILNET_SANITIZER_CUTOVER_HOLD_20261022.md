# AssistX Tailnet TCP:8000 sanitizer replacement — physically qualified candidate
**October 22, 2026 | Draft PR #425 | ingress P0 issue #149 | Deployment HOLD**

## Verified live exposure and client dependencies

Fleet Commander confirmed on x1-370 that `control-room-ts-proxy.service` is active and runs `socat` from the host's Tailnet IPv4 TCP:8000 to `127.0.0.1:8000`. The Docker-published AssistX backend itself listens only on localhost, but the raw TCP forward bypasses any authenticated Tailscale Serve header-generation/stripping layer. The backend's configured trusted header is exactly `Tailscale-User-Login`, while Basic username and password are also configured. The running API has **not** enabled emergency legacy-header denial or signed owner binding.

A fresh read-only grep of local fleet repositories found OpenCode bridge `dispatch_assistx_task.py` and `sync_configs_to_assistx.py` defaulting to the Tailnet TCP:8000 endpoint and using **HTTP Basic** for their application requests. `assistx-tailnet-home/server.py` probes a local `/health` route. Tailscale Serve also has separate route-scoped mobile paths on HTTPS:8443; those proxy directly to the localhost API rather than through the raw Tailnet TCP listener. These source references do **not** prove that all current clients or WebSocket users are compatible or that no clients rely on the legacy trusted header.

The current raw TCP listener forwards an arbitrary client-supplied `Tailscale-User-Login` HTTP header to AssistX. Its checked-in legacy authentication function treats the configured header value as the user before Basic authentication. This is a real **conditionally exploitable trust path**, without a live forged-credential test or claim of Internet exposure.

## Tested Caddy replacement

A standalone, staged configuration is in `scripts/assistx-tailnet-header-sanitizer.Caddyfile`. It listens only at the configured Tailnet IP:8000, reverse-proxies to 127.0.0.1:8000, **removes `Tailscale-User-Login` and five alternative forwarded identity headers**, and preserves ordinary HTTP Basic authentication and other request traffic. No privileged Caddy admin API or automatic TLS management is enabled in the candidate. WireGuard/Tailscale still owns Tailnet transport access; the Caddy ingress does not itself authenticate users.

The file is inert and has **not been installed** as a systemd service or activated. The currently running `socat` service still owns port 8000.

The disposable `tests/assistx_tailnet_sanitizer_canary.py --physical-loopback` starts the actual installed Caddy binary on a *random loopback test port*, first against a synthetic HTTP/WebSocket/SSE backend and then against the running AssistX backend (only public GET `/health` and unauthenticated GET `/command-center` through the sanitized proxy). It never binds the production Tailnet interface, contacts a real model, sends a genuine user credential, writes a task, or changes the production service.

**11/11 physical checks passed:**
- Caddyfile validates with installed Caddy 2.6.2.
- GET and POST remove all selected client identity headers.
- Synthetic HTTP Basic header reaches a synthetic backend intact.
- `text/event-stream` SSE is preserved, and its synthetic upstream received no identity header.
- HTTP 101 WebSocket upgrade succeeds; the synthetic upstream verifies no client identity header reached it.
- Real AssistX `/health` responds through the new *loopback-only* candidate.
- A synthetic forgery delivered through that candidate to protected `/command-center` returns HTTP 401 without sending the forged header to the live backend.
- Test services, configuration and sockets are cleaned up.
- Running production AssistX remains on exact image `sha256:74df40c5c720dbef283f83eaf69eae585212748ca3889be508eaa9c88cd5b4c4`.

The separate `scripts/assistx_tailnet_sanitizer_preflight.py --physical-readonly` checks the active `socat` service's exact Tailnet-to-loopback target, live AssistX image, expected identity-header **name only**, Basic-credential presence **without values**, stripping policy, and validates the staged Caddyfile using the actual Tailnet IP. It reports `automatic_cutover_authorized=false` even if all structural checks pass; the gateway and rollback are not yet qualified for all real clients.

## Bounded cutover / rollback handoff — NOT EXECUTED

1. **Pin source and binary.** Snapshot the exact existing systemd unit content and the running container SHA, and place the reviewed Caddyfile at an immutable release path. Verify `caddy version`, the pinned release SHA, `tailscale ip -4`, and strict `caddy validate`. Do not replace the active unit with a file from a floating branch.
2. **Map essential consumers.** Check OpenCode task dispatch and config sync using a safe read-only/permission-scoped request, existing authenticated mobile routes, SSE/WebSocket clients, any recovery-island pushes, and any other clients using raw TCP:8000. Confirm no authorized client depends solely on a client-supplied `Tailscale-User-Login` header. Do not print or export credentials.
3. **Prepare the service swap.** Stage a dedicated user-systemd Caddy unit for the exact Tailnet IPv4 listener and a separately pinned, previously accepted **sanitizing Caddy** recovery unit/config. Keep an immutable snapshot of the old `control-room-ts-proxy.service` only for forensic comparison, **not as an executable rollback**. Require a boot-persistence fence: the legacy socat unit must be inactive and disabled before the new listener is marked safe. Validate exclusive IP:8000 ownership and bounded HTTP/WS/SSE health checks; do not alter the separate authenticated Tailscale Serve :8443 routes.
4. **Only after the preceding steps pass:** during an explicitly authorized maintenance interval, retire the old unit's boot enablement and stop the old forwarder, start the pinned Caddy ingress, verify live exclusive port ownership, health, and correct protected-route denial for header-only requests while real Basic authentication remains functional, then test a scoped authorized client. If a step fails, stop the broken candidate and recover only through the **pinned sanitizing Caddy fallback** on the same Tailnet port; if that also fails, remain **fail-closed/offline with a recovery incident**, not the old socat forwarder. Do not claim success from synthetic tests alone.
5. **Post-cutover verification:** use a cutover-aware check of the **new** process/port owner (the old-service preflight expects socat and cannot certify Caddy), independently read back the exact active Caddy config and trusted-header stripping, and verify the socat unit stays inactive/disabled through user-systemd restart or other approved boot-persistence verification. Confirm no unexpected parallel direct listener, verify the separate Tailscale Serve :8443 mobile SSO still authenticates, and independently qualify Tailscale ACLs and other localhost callers. Treat any unverified result as HOLD; never return to raw socat as a security rollback.

Important: A Caddy ingress can mitigate Tailnet **raw-port** header spoofing without changing the legacy backend and without disabling Tailscale Serve's separately authenticated :8443 mobile routes. It does not secure every possible local caller, does not implement independent signed gateway owner custody, and does not demonstrate full client compatibility. Therefore it is a staged candidate, **not a completed live vulnerability remediation**.

## Other P0 gates

Production Hermes still has 16 legacy mounts including unsafe writable storage. Independently administered signing/rollback-resistant high-water, real two-human task claims and receipts, partition-time revocation/fencing, narrow signed RO corpus + isolated RW scratch, and signed release/rollback readiness remain unresolved. Keep draft PR #425 and issue #414 open, without production deployment.


## October 23: same-port handover and safe rollback rehearsal

The additional `tests/assistx_tailnet_sameport_rollback_canary.py --physical-loopback` runs real Caddy and socat processes sequentially on a random loopback-only port with an in-memory synthetic HTTP backend. It demonstrates three stages: raw socat forwards a fake identity header; sanitizing Caddy removes that header while preserving synthetic Basic authorization; after an orderly stop, a second sanitizing Caddy instance uses the same port and continues to remove that header. The test cleans up every disposable listener. **5/5 physical checks passed.** No actual Tailscale listener, systemd unit, credential, database or production API changed.

A fallback to **the original socat** is not a security-safe rollback: it restores the header spoofing trust path. The correct fallback must instead be a pinned, previously accepted Caddy sanitizer. The experiment does not prove the behavior of all real clients, existing long-running WebSocket sessions, production maintenance-window handover, or real authenticated access; those remain prerequisites. Production authorization stays HOLD.


### OpenCode bridge compatibility added October 23

The disposable same-port Caddy rehearsal also compiles and invokes the exact checked-in \`_post\` function from \`scripts/opencode-bridge/dispatch_assistx_task.py\` without importing or executing its module-level workflow. It sends only a synthetic task-shaped POST to the isolated loopback HTTP echo backend through the sanitizing Caddy listener, carrying disposable Basic credentials. The backend confirms a POST arrived, Basic Authorization survived, and no client identity header arrived. No real task or model is dispatched. This is positive evidence for one real bridge implementation's request construction, not acceptance of the full live OpenCode workflow or its existing stored credentials.


### OpenCode configuration-sync compatibility — October 23

The disposable same-port sanitizer rehearsal invokes the **exact checked-in** `_post` function from `scripts/opencode-bridge/sync_configs_to_assistx.py`, in addition to the previously verified dispatch function. It sends the synthetic model-endpoint registration and probe POST requests only to a disposable loopback HTTP echo backend, with synthetic Basic credentials. The sanitized backend confirms two POSTs, Basic authentication survives, and no `Tailscale-User-Login` identity header arrives. Neither `run_sync` nor live fleet scans, models, router refreshes, or task writes run. The synthetic same-port rehearsal now passes **7/7** checks.

This proves request construction and forwarding, **not** real client login, a live endpoint registration, a complete production cutover, or independent rollback authorization. The live `control-room-ts-proxy.service`, production API, and Hermes runtime remain unchanged; release HOLD remains in effect.


### Read-only live Basic-auth acceptance through sanitizer — October 23

Fleet Commander ran `tests/assistx_tailnet_real_basic_canary.py --physical-readonly-basic` with a **disposable Caddy process on a random loopback port**, forwarding only to the running localhost AssistX API. The canary pinned the original API and Hermes image IDs and active existing `control-room-ts-proxy.service`; it read the already configured Basic-auth username and password **into its own process memory only** from local Docker metadata, without printing either value, writing credential files, or exporting them to a plugin result. It made only HTTP **GET `/command-center`** requests through the disposable sanitizer, discarded response bodies without reading them, and retained only status comparisons.

**7/7 physical checks passed:** anonymous request denied (401); valid existing Basic credentials alone accepted (200); valid Basic plus an extra synthetic forged `Tailscale-User-Login` header still accepted as Basic (200), demonstrating that Caddy strips the untrusted header; the same forged header **without** Basic rejected (401); actual Caddy configuration validated; disposable process/config cleaned; production API/Hermes images and original Tailnet forwarder remained unchanged. Separate unit/static tests verify the CLI defaults inert, expected images and header name, duplicate Docker env keys fail closed, only loopback GETs occur, and no response body/credential values are logged.

This qualifies one **real read-only Basic-authenticated application route**, not full client behavior, long-lived connections, actual task/agent side effects, all existing consumers, or an on-port systemd handover. It does not remove the existing live insecure socat ingress. Real same-port cutover and pinned sanitizer-to-sanitizer rollback are still NO-GO until a bounded operational acceptance completes.


### October 23 — Lenovo-originating Tailnet ingress qualification

An opt-in disposable canary, `tests/assistx_tailnet_lenovo_ingress_canary.py --physical-tailnet-lenovo`, qualifies an actual network round trip from Lenovo into a temporary x1-370 Caddy listener on a distinct random Tailnet TCP port. It binds only x1-370's verified Tailnet IPv4 address, allows GET requests to `/health`, `/command-center`, and its canonical redirected protected route `/control-room`, strips `Tailscale-User-Login` plus five alternate forwarded identity headers, and denies other paths with Caddy HTTP 404. The previously discovered `/command-center` to `/control-room` redirect must be allowed during testing; an initial fail-closed rehearsal rejected the redirected route with 404 and was corrected before acceptance.

Lenovo connects with existing pinned-host-key SSH in noninteractive BatchMode and makes only three bounded GET requests: `/health` (200), `/control-room` with a synthetic fake identity header (401), and `/openapi.json` (404, intentionally excluded from the disposable allowlist). No actual user credentials, task POSTs or protected response bodies are sent or logged. **9/9 physical checks PASS**: off-host health, spoof rejection and route allowlist, temporary listener cleanup, production proxy still active and exact original API/Hermes images unchanged. The existing `control-room-ts-proxy.service` retained production TCP:8000 throughout.

This establishes off-host Tailnet network reachability and sanitized rejection, supplementing prior local Basic-auth, SSE/WebSocket, OpenCode request-construction and same-port rollback rehearsals. The temporary listener is not the production listener; the test does not establish full real-client compatibility, Tailscale ACL security or independent key custody. Real ingress cutover stays **HOLD**. Restoring the raw socat service would restore the old trusted-header risk and must not be mislabeled a security-safe rollback.


### October 23 — exact release-Caddyfile rollback parity

The same-port synthetic `socat` → Caddy → sanitizing Caddy rollback harness now derives **both** Caddy instances by rebinding the exact reviewed `scripts/assistx-tailnet-header-sanitizer.Caddyfile` into *loopback-only* ephemeral endpoints, instead of generating an independent test configuration. The checked-in source bytes are pinned to SHA-256 `2efa50602a821a9dffc6704496eb1cd0c0988e81c2f1fce65c3b9ad249e2a444`; altered source, missing identity-header removal rules, ambiguous reverse-proxy structure, production port reuse and symlinked source are rejected. Only listener/upstream addresses are substituted for the disposable synthetic ports. This removes a test/production configuration-divergence risk without changing the actual release Caddyfile.

**Physical readback (x1-370): 7/7 loopback same-port tests PASS; 33 focused sanitizer unit/static tests PASS; Ruff clean.** Both checked-in OpenCode bridge `_post` request constructors still pass synthetic Basic auth without forwarding forged identity. The running Tailnet :8000 `socat`, API and Hermes remain untouched. This is strengthened **candidate reproducibility**, not a live cutover, off-host authorized client compatibility, or an independently qualified service restart/rollback. Ingress P0 #149 stays OPEN and PR #425 stays DRAFT / HOLD.


### October 23 — reproducible transient user-systemd Basic-auth + sanitized fallback proof

New opt-in `tests/assistx_tailnet_systemd_auth_canary.py --physical-user-systemd` uses **two sequential temporary user-systemd Caddy units**, each on the *same disposable loopback high port* and using a SHA-256-pinned derivative of the reviewed release Caddyfile. The original API at 127.0.0.1:8000 remains untouched, and the release candidate never binds the production Tailnet IP or port. The script is inert without its physical flag.

Each temporary unit is checked against the actual configured AssistX Basic credentials **in local process memory only** and with read-only GET requests: anonymous requests return 401; valid Basic alone returns 200; a forged synthetic identity header alone returns 401; and valid Basic plus that forged header returns 200 after stripping. It stops and verifies release of the isolated listener before bringing up the second sanitized fallback. Transient units use `Restart=no`, `NoNewPrivileges=yes`, and a 45-second runtime limit. Every unit has a generated `assistx-sanitizer-proof-<hex>.service` name, and cleanup is limited to those generated names. No real task/model requests are performed and no protected response bodies or Basic values are logged.

**Physical x1-370 readback: 6/6 checks passed** (release derivative validation, two authenticated/denied phases, two port releases, original runtime state unchanged); **10/10 new unit checks passed**; Ruff clean. No disposable units remained and the live raw `socat` service stayed active. This is a reproducible operator-gated maintenance rehearsal, not an authenticated *off-host* release test or production port-8000 cutover. **The raw Tailnet identity-spoofing risk remains unresolved in production.** Keep PR #425 DRAFT / HOLD, ingress P0 #149 OPEN, and use a pinned sanitizing Caddy fallback rather than unsafe socat rollback.


### October 23 — real Basic-authenticated Lenovo network-origin canary

The new opt-in `tests/assistx_tailnet_lenovo_socks_basic_canary.py --physical-lenovo-socks-basic` uses the reviewed, SHA-256-pinned release Caddyfile to create a short-lived, high-port **Tailnet-only** Caddy listener. Its additional canary-only allowlist restricts that listener to read-only GET `/health` and `/control-room` and returns 404 otherwise. It does **not** bind the production port 8000, alter Tailscale Serve, restart API/Hermes, dispatch a task or read protected response bodies.

Unlike the prior anonymous Lenovo probe, the canary exercises **real configured Basic authentication over a connection originating from Lenovo**. A temporary noninteractive SSH dynamic SOCKS relay uses Lenovo's remote TCP egress. A separate challenge listener on x1-370 confirms the peer IP equals Lenovo's independently read-back Tailnet address. The client sends Basic credentials from x1-370 process memory through the SSH-relayed TCP stream; the credentials necessarily transit Lenovo's network stack but are **not placed in Lenovo environment variables, command arguments, files, or logs**. No values are returned to ChatGPT or included in the result. The temporary SSH and Caddy processes and configuration are removed.

**9/9 physical checks PASS**: pinned release Caddyfile validates; Lenovo-SOCKS /health returns 200; network peer confirmed; protected anonymous GET 401; forged identity-only GET 401; real Basic-only GET 200; valid Basic plus synthetic forged identity GET 200 with the header sanitized; both disposable processes stop; production runtimes and ingress unchanged. **96 related unit/static tests PASS** (one unrelated Starlette deprecation warning) and Ruff clean in the validation checkout.

This closes the **Lenovo-originated remote Basic read-only authentication and header-spoofing** test gap for a disposable ingress derived from the staged Caddyfile. It is not proof that all genuine Lenovo/OpenCode/mobile clients have the correct secrets, will survive the actual port-8000 change, or can preserve long-running SSE/WebSocket sessions across a restart. The real port-8000 service switch, safe sanitizer-to-sanitizer rollback, and independent owner/signer/claim/mount gates remain HOLD. The existing raw socat forwarder remains the production vulnerability until replaced.
