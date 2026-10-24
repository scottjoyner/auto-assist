# AssistX Tailnet TCP:8000 sanitizer replacement — physically qualified candidate
**October 22, 2026 | Draft PR #425 | P0 issue #414 | Deployment HOLD**

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
3. **Prepare the service swap.** Stage a dedicated user-systemd Caddy unit configured for the exact Tailnet IPv4 listener and a rollback copy of the original `control-room-ts-proxy.service` socat unit. Validate non-overlapping port ownership; avoid running both on the same IP:8000. Preflight service dependencies and define bounded HTTP/WS/SSE health tests.
4. **Only after the preceding steps pass:** during a controlled maintenance interval, stop the old forwarder, start the pinned Caddy ingress, verify the public schema and health route, confirm synthetic identity-header-only requests are denied while valid Basic without the header works, then test a scoped real client. If any step fails, stop only the candidate and restore/start the exact old service **with explicit acknowledgment that the old header risk returns**. Avoid an unaudited long-lived half-switched configuration.
5. **Post-cutover verification:** repeat release preflight against the new service, compare active process/port binding and inspect the *actual* Caddy runtime config, confirm no unexpected parallel direct listener, then independently qualify Tailscale ACLs and other localhost callers. Rollback is not fully safe from a security perspective while the original header-trusting service remains available.

Important: A Caddy ingress can mitigate Tailnet **raw-port** header spoofing without changing the legacy backend and without disabling Tailscale Serve's separately authenticated :8443 mobile routes. It does not secure every possible local caller, does not implement independent signed gateway owner custody, and does not demonstrate full client compatibility. Therefore it is a staged candidate, **not a completed live vulnerability remediation**.

## Other P0 gates

Production Hermes still has 16 legacy mounts including unsafe writable storage. Independently administered signing/rollback-resistant high-water, real two-human task claims and receipts, partition-time revocation/fencing, narrow signed RO corpus + isolated RW scratch, and signed release/rollback readiness remain unresolved. Keep draft PR #425 and issue #414 open, without production deployment.
