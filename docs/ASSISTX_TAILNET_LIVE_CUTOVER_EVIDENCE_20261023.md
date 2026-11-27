# AssistX live sanitized Tailnet ingress — October 23, 2026

**Release disposition: security mitigation ACTIVE, broad release HOLD.** Source PR #425. Ingress-auth P0 #149 remains OPEN; independent Hermes writable-mount P0 #414 remains OPEN.

## Exact physically witnessed transition

At approximately 22:25 ET, the guarded x1-370 cutover from commit `033702ff34148cbe8f55db744a00e69123deee41` disabled then stopped the `control-room-ts-proxy.service` raw socat listener, enabled/started the pinned primary Caddy, and attempted read-only postcutover certification. The Caddy service itself ran, but its checker required reading `/proc/<Caddy PID>/exe` and `/proc/<Caddy PID>/cmdline`: kernel permissions denied the direct process lookup, so the static certification returned false. The guarded fail-closed path disabled/stopped primary and enabled/started the **separate pinned sanitized Caddy fallback**, not socat. `set -E` initially caused duplicate inherited ERR-trap execution; later source removes that inheritance. Do not imply the revised trap has been exercised in production.

The original raw proxy unit was preserved as read-only forensic evidence under `/home/scott/.config/assistx-tailnet-sanitizer/retired-control-room-ts-proxy.service`, SHA-256 `942e458953142383e8ccad8d8921d8b637155d39c651d6a7c054ea85887b1ebd`. Its user-systemd unit path was replaced by a `/dev/null` mask and reloaded; readback shows `LoadState=masked`, `ActiveState=inactive`, `UnitFileState=masked`. The original service cannot restart through normal systemd boot/service activation. No actual reboot was performed.

Live fallback `assistx-tailnet-sanitizer-fallback.service` is ACTIVE/boot ENABLED, owning **exactly the Tailnet `100.64.43.123:8000` listener** with UID=1000, positive inode and the exact fallback service cgroup. The backend remains bound to `127.0.0.1:8000` under Docker, not directly exposed on external interfaces. Fallback Caddy configuration SHA-256 `c4136364469a45f918c8e9a33ce5268f57dc4ee39b030012af4f66aaf9b186b2` matches the reviewed strict-Tailnet-only source; Caddy binary SHA-256 `9681d054b6e5137d53f6d027188b349e0603fd12ea6334bd0d49da6755432f1b`. The full exact fallback user unit bytes are SHA-256 `dd73f26042f1626a19875a32532f90fe539617d0778ba1e4eaf9c74fa181e263`, confirmed by systemd `ExecStart`, unit fragment path, and running socket cgroup. The fallback remained running and healthy after `systemctl --user daemon-reload`.

## Read-only real-client evidence

- Tailnet and local API `GET /health` returned HTTP 200.
- Protected anonymous `GET /control-room` returned HTTP 401 on x1-370 **and on Lenovo**.
- Synthetic attacker-controlled `Tailscale-User-Login` header WITHOUT Basic credentials returned HTTP 401 on x1-370 **and on Lenovo** after the cutover.
- Separate HTTPS Tailnet Serve `:8443/api/v1/auth/whoami` returned HTTP 200 with `authenticated=true`, `provider=tailscale` from x1-370 and Lenovo; no names, tokens, login values or response bodies were logged. Exact five scoped Serve API paths remain unchanged.
- Original AssistX and Hermes container image digests and 10/16 mount counts did not change; no task dispatch or writes were executed.

**Unverified:** real Basic authorization on the newly switched production ingress. A credential-bearing validation command was blocked by the tool's safety checks; earlier **disposable** Basic-auth tests passing 9/9 cannot substitute for live post-switch Basic acceptance. Long-lived WebSocket/SSE client continuity, actual reboot/restart persistence, independent signer/user provenance, signed claims, revocation and Hermes mount isolation also remain unverified.

## Independent read-only follow-up attestation

Updated `scripts/assistx_tailnet_postcutover_attestation.py --physical-readonly --candidate fallback` explicitly recognizes non-root `/proc` executable lookup denial, without asserting nonexistent direct executable proof. It requires the SHA-256-pinned unit file, exact systemd startup command, positive PID, matching systemd cgroup + socket UID/inode, matched binary and config hashes, the original unit **masked**, and the separate five mobile Serve paths. The observed fallback now passes `postcutover_static_gate_met=true` while still returning `decision=HOLD`, `real_offhost_auth_verified=false`, `safe_fallback_activated_and_qualified=false` and `production_release_authorized=false`. This is an **evidence-qualified mitigation**, not general Hermes or AssistX release approval.

**Next smallest safe acceptance:** read-only live Basic from an authorized existing client without exposing or extracting its credentials; monitor fallback uptime and boot-persistence at a future approved reboot. Keep the currently healthy fallback in place; do not start the primary on the same socket or restore raw socat. Close ingress issue #149 only after all its authentication-provenance criteria are satisfied; address Hermes mount issue #414 separately.
