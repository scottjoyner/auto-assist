# AssistX Tailnet ingress boot-persistence guard — release HOLD
**October 23, 2026 | PR #425 | P0 #414 | NO PRODUCTION CHANGE**

Fleet Commander read-only inspection confirmed the original `control-room-ts-proxy.service` is both **active** and **enabled at boot**. It runs `/usr/bin/socat`, forwarding the x1-370 Tailnet IPv4 `:8000` to the original AssistX loopback API. Consequently, simply stopping the old unit for a temporary Caddy cutover is not a durable remediation: the unsafe raw forwarder may return on login or reboot unless its startup is retired.

The new `scripts/assistx_tailnet_ingress_persistence_guard.py --physical-readonly` reports only bounded metadata. It checks current `systemctl --user` active/enabled state, expected legacy ExecStart, socket binding inventory, Docker's loopback-only API publishing, reviewed Caddyfile and Caddy binary SHA-256 pins, and counts separately configured Tailscale Serve routes that target the API. It never emits credential values, changes Tailscale or systemd, sends tasks, or changes containers. Its default invocation is inert and its answer is **always HOLD**, not deployment approval.

**Exact live observation:** `RAW_FORWARDER_BOOT_PERSISTENT`; original service active and enabled; two expected port-8000 listeners (Tailnet raw proxy and local API); API Docker publishing confined to 127.0.0.1; no unexpected third port-8000 listener; Caddy candidate configuration and binary hashes matched pins; **five separately mapped Tailscale Serve routes** target the localhost API. Tailscale Serve authorization is independent and is not certified by this guard.

**Acceptance remaining before activating a production sanitizer:**
- Stage and verify a version-pinned Caddy service and an independently pinned *sanitizing Caddy* fallback that can safely regain the same Tailnet port.
- Fence the legacy `socat` unit's **boot-time enablement** as part of the controlled cutover; verify it stays inactive and disabled across user-systemd daemon reload/restart and an approved boot simulation or actual reboot. Do not re-enable socat as a security rollback.
- Verify actual port ownership, upstream identity stripping, real off-host Basic authentication, denied header-only impersonation, essential OpenCode/mobile traffic, and clean listener/socket closure.
- Treat unexpected listener identity, stale pins, failed auth, or missing recovery ingress as HOLD. Neither code-level CI nor a no-op boot-state report authorizes a production change.

Production ingress remains unchanged. The wider Hermes mount isolation, signed identity provenance, owner binding, revocation and fencing gates are separate NO-GO items. Keep PR #425 draft and issue #414 open.
