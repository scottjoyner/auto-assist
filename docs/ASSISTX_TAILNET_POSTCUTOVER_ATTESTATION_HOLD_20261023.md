# AssistX sanitized ingress — read-only post-cutover attestation

**October 23, 2026 | PR #425 | Security P0 #414 | HOLD**

The old `assistx_tailnet_sanitizer_preflight.py` assumes the original raw `socat` service is still running. It therefore cannot certify the *new* Caddy service even if the live cutover succeeds. The new inert-by-default `scripts/assistx_tailnet_postcutover_attestation.py --physical-readonly` inspects only local metadata after an independently controlled cutover; it never starts, stops, installs or disables systemd units, changes Docker/Tailscale/Serve, reads credentials, or performs API tasks.

It requires the legacy `control-room-ts-proxy.service` to be **inactive and no longer enabled at boot**, and the future `assistx-tailnet-sanitizer.service` to be running and enabled. It checks the Caddy process's actual executable and `--config` invocation against an explicit deployment path, SHA-256 pins both Caddy binary and staged release config, checks exclusive expected localhost/Tailnet port-8000 listener addresses, and confirms the five mobile Serve paths still map to the loopback API without wider AssistX mounts.

The expected future Caddy unit and config are **not installed**. The current live readback is correctly **HOLD**, with `legacy_unit_inactive=false`, `legacy_unit_cannot_start_at_boot=false`, `pinned_caddy_process_verified=false` and `pinned_caddy_config_verified=false`. All legacy API/Hermes/Serve runtime state is unchanged.

The static attestation does **not** claim live listener ownership, authenticated remote client acceptance, header-only spoof denial, boot-cycle persistence, or an activated safe Caddy recovery fallback. Its disposition is **always HOLD** until separate operator-verified proof is attached; even a perfect synthetic input to the assessment function cannot set `production_release_authorized=true`. The main ingress P0 and Hermes custody/mount/signed-identity blockers remain open.
