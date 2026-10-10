#!/usr/bin/env bash
# Offline-safe by default. Never deploy based only on a trusted-header claim.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVE_PORT="${KIPNERTER_GATEWAY_SERVE_PORT:-8443}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 3
}

[[ "$SERVE_PORT" == "8443" ]] || fail "approved mobile gateway port is 8443"
[[ "${ASSISTX_API_BIND:-}" == "127.0.0.1" ]] || fail "AssistX backend must be loopback-only"
[[ "${TRUSTED_AUTH_HEADER:-}" == "Tailscale-User-Login" ]] || fail "unexpected identity header"
[[ "${KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE:-1}" == "0" ]] || fail "model override not disabled"
[[ -n "${KIPNERTER_TAILNET_ALLOWED_LOGINS:-}" ]] || fail "Tailnet allowed-logins allowlist missing"

# An operator must pin the exact backend source. This is not a floating tag,
# branch, or whatever is checked out on a production server.
required="${KIPNERTER_GATEWAY_SOURCE_SHA:-}"
[[ "$required" =~ ^[0-9a-f]{40}$ ]] || fail "KIPNERTER_GATEWAY_SOURCE_SHA must be an exact 40-hex commit"
actual="$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null || true)"
[[ "$actual" == "$required" ]] || fail "checked-out backend does not match required backend SHA"
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]] || fail "working tree is dirty"

# This is a confirmation *gate*, not evidence. The separate signed/owned
# physical negative-test witness must be examined by an operator.
[[ "${KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED:-0}" == "1" ]] || fail \
  "legacy x1-370 Caddy Tailscale-header fence is not confirmed (scottjoyner/Sophia#13)"

# A missing or invalid external witness is a HARD HOLD before any mutation.
# It must be independently owned and refer to this exact deployment source.
witness="${KIPNERTER_GATEWAY_NEGATIVE_WITNESS_FILE:-}"
[[ -n "$witness" && -f "$witness" && ! -L "$witness" ]] || \
  fail "physical negative-ingress witness unavailable"
python3 - "$witness" "$required" <<'PY'
import json
import os
import sys
witness, sha = sys.argv[1:]
if os.path.getsize(witness) > 16384:
    raise SystemExit("oversized ingress witness")
with open(witness, encoding="utf-8") as handle:
    proof = json.load(handle)
if not isinstance(proof, dict) or proof.get("schema") != "assistx-ingress-negative-v1":
    raise SystemExit("invalid ingress witness schema")
if proof.get("source_sha") != sha:
    raise SystemExit("ingress witness references a different source SHA")
for key in (
    "independent_reviewer",
    "spoofed_identity_header_denied",
    "legacy_caddy_header_stripping_verified",
    "loopback_nonproxy_denied",
    "mobile_path_scope_checked",
    "operator_approval",
):
    value = proof.get(key)
    if key == "independent_reviewer":
        if not isinstance(value, str) or not value.strip():
            raise SystemExit("independent reviewer identity missing")
    elif value is not True:
        raise SystemExit("ingress witness did not pass " + key)
PY

# Read-only preflight is the default. Do NOT automatically change Tailnet Serve.
if [[ "${KIPNERTER_GATEWAY_APPLY:-0}" != "1" ]]; then
  printf 'kipnerter_gateway_preflight=passed; apply=false; no Serve changes\n'
  exit 0
fi

# Applying remains a separately deliberate, operator-controlled action. This
# helper does not adjust Caddy, rewrite authentication, or grant API authority.
[[ "${KIPNERTER_GATEWAY_APPLY_OPERATOR_APPROVED:-0}" == "1" ]] || \
  fail "separate operator apply approval missing"

# A user of this script is responsible for physically inspecting ingress.
# It is NOT proof that a forged client header cannot reach other endpoints.
"$ROOT/scripts/configure-kipnerter-tailnet-serve.sh"
"$ROOT/scripts/verify-kipnerter-tailnet-gateway.sh"
