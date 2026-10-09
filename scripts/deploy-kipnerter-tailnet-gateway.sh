#!/usr/bin/env bash
# Kipnerter gateway release preflight — FAIL CLOSED, NO SERVE OR DEPLOY ACTION.
# Tracked helper restored for review; this does not grant or assert #149 proof.
set -euo pipefail

fail() {
  printf 'HOLD: %s\n' "$*" >&2
  exit 78
}

command -v git >/dev/null 2>&1 || fail "git missing"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null)" || fail "source checkout not a git repository"
actual_sha="$(git -C "$repo_root" rev-parse --verify HEAD 2>/dev/null)" || fail "source HEAD unavailable"
required_sha="${KIPNERTER_GATEWAY_SOURCE_SHA:-}"
if [[ ! "$required_sha" =~ ^[0-9a-f]{40}$ ]]; then
  fail "KIPNERTER_GATEWAY_SOURCE_SHA must pin a 40-character Git SHA"
fi
if [[ "$actual_sha" != "$required_sha" ]]; then
  fail "checked-out source does not match required backend SHA"
fi
# A clean exact source tree is a necessary condition, not deployment authority.
if [[ -n "$(git -C "$repo_root" status --porcelain --untracked-files=all)" ]]; then
  fail "working tree is dirty"
fi

serve_port="${KIPNERTER_GATEWAY_SERVE_PORT:-8443}"
[[ "$serve_port" == "8443" ]] || fail "Tailnet port must stay 8443, not Caddy :443"
[[ "${ASSISTX_API_BIND:-}" == "127.0.0.1" ]] ||
  fail "AssistX backend must be bound to 127.0.0.1"
[[ "${TRUSTED_AUTH_HEADER:-}" == "Tailscale-User-Login" ]] ||
  fail "unrecognized trusted-header configuration"
[[ "${KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE:-1}" == "0" ]] ||
  fail "mobile model override must remain disabled"
[[ -n "${KIPNERTER_TAILNET_ALLOWED_LOGINS:-}" ]] ||
  fail "explicit Tailnet operator login allowlist is required"

if [[ "${KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED:-0}" != "1" ]]; then
  fail "legacy x1-370 Caddy Tailscale-header fence is not confirmed (scottjoyner/Sophia#13)"
fi
if [[ "${KIPNERTER_GATEWAY_AUTH_NEGATIVE_TEST_VERIFIED:-0}" != "1" ]]; then
  fail "independent physical forged-header negative witness is not verified; see auto-assist#149"
fi

# These environment statements are NOT a signed physical proof or authorization
# to configure Tailscale Serve. A future source-owned release system must verify
# actual ingress/header stripping and perform an separately approved atomic apply.
# Do not call configure-kipnerter-tailnet-serve.sh from this preflight or reset
# unrelated root mounts.
fail "all declarative preflights satisfied but NO deployment authority: hard HOLD on #149"
