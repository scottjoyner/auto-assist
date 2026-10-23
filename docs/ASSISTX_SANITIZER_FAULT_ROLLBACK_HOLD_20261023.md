# AssistX Tailnet sanitizer — transient service failure and safe rollback proof

**October 23, 2026 — release decision: HOLD.** This research slice belongs to draft [auto-assist PR #425](https://github.com/scottjoyner/auto-assist/pull/425) and P0 issue #414. It is a test of an actual user-systemd **failed Caddy upgrade** followed by a recovery to a *sanitizing* Caddy process. Nothing ran on the production Tailnet port 8000.

## Why this qualification was needed

Previous canaries demonstrated two healthy user-systemd Caddy instances sequentially owning the same disposable port and preserving real configured Basic authentication while rejecting synthetic forged `Tailscale-User-Login` identities. That success did not exercise a failed replacement unit between those two stages. Restoring the old production raw `socat` service as a fallback would restore the original trusted-header spoofing path and cannot be represented as a safe security rollback.

## Fault-injection execution

Run **only explicitly** on non-root x1-370:

```
PYTHONPATH=src:tests:scripts python3 tests/assistx_tailnet_sanitizer_fault_rollback_canary.py --physical-disposable-user-systemd
```

Without `--physical-disposable-user-systemd`, the script only prints an inert HOLD.

The test pins the already-reviewed release Caddyfile through the existing `isolated_config` helper, substitutes a **random localhost-only high port**, and uses the production API at `127.0.0.1:8000` solely for read-only GET requests. Existing configured Basic auth values are held only in the transient test process's memory; no credentials, protected response bodies, or real task data are printed, stored in generated units or transmitted to plugin output.

1. The initial transient non-root user-systemd Caddy service starts from a Caddyfile derived from the release sanitizer and passes anonymous 401, existing Basic 200, forged header 401, and Basic plus forged header 200. The process then stops and releases the test port.
2. An intentionally invalid Caddyfile is verified to fail `caddy validate`. A bounded, non-restarting transient user-systemd unit is actually started with that invalid configuration. The test waits for the failed/inactive unit **and absence of any listener on the disposable port**.
3. A newly named, independently started transient Caddy fallback uses the **pinned sanitizing configuration**, not socat. It reacquires the same port, repeats real Basic and header-denial checks, then terminates and releases that port.
4. Finally, both original production container images and the active original `control-room-ts-proxy.service` are reverified unchanged. Only disposable `assistx-sanitizer-proof-<12-hex>.service` names can be targeted for cleanup.

**Physical x1-370 result: 7/7 checks PASS.** The related 39 focused pytest tests PASS and the new files satisfy Ruff checks. The high-port fallback did not introduce a header-only authentication path.

## Non-claims and next operational gate

This proof does **not** authorize switching the current live `socat` Tailnet :8000 listener. It does not preserve existing persistent WebSocket/SSE connections through an actual service restart, map all real callers, or prove live client behavior during a real production maintenance window. Most importantly, the running API still trusts the raw legacy header via the original socat forward; the production P0 remains open.

The next release step is a separately controlled, pinned-image and pinned-config service cutover with a measured compatibility and rollback plan. A security-safe fallback must remain a sanitizing gateway, not unmodified socat. Independently authenticated user signing, rollback-resistant custody, owner-bound task receipts/fencing, and Hermes 16-mount source/scratch isolation are separate HOLD gates.
