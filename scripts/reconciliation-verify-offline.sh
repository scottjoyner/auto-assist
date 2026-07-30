#!/usr/bin/env bash
# Fail closed when rendered migration configuration contains a public inference path,
# duplicate authority, or runtime mutation flag, or when shadow services are unavailable.
set -euo pipefail

ROUTER_URL="${RECONCILIATION_NEW_ROUTER_URL:-http://127.0.0.1:18088}"
ASSISTX_URL="${RECONCILIATION_NEW_ASSISTX_URL:-http://127.0.0.1:18000}"
ENV_FILE="${RECONCILIATION_ENV_FILE:-}"
if [ -z "$ENV_FILE" ] && [ -f deploy/reconciliation.env ]; then
  ENV_FILE=deploy/reconciliation.env
fi
if [ "$#" -gt 0 ]; then
  RAW_SCAN_PATHS=("$@")
else
  RAW_SCAN_PATHS=(.)
fi

# Repository roots are converted to the rendered shadow plan or the narrow
# reconciliation provider/policy files. This avoids treating known historical
# base configuration as the deployed result while still validating the exact
# merged configuration that Docker will run.
SCAN_PATHS=()
for path in "${RAW_SCAN_PATHS[@]}"; do
  if [ -d "$path" ] && [ -f "$path/artifacts/reconciliation-render/assistx-router.yaml" ]; then
    SCAN_PATHS+=("$path/artifacts/reconciliation-render/assistx-router.yaml")
  elif [ -d "$path" ] && [ -f "$path/providers.reconciliation.yaml" ]; then
    SCAN_PATHS+=("$path/providers.reconciliation.yaml")
    [ -f "$path/policies.yaml" ] && SCAN_PATHS+=("$path/policies.yaml")
  else
    SCAN_PATHS+=("$path")
  fi
done

# Empty credential declarations are allowed and verified separately below. What is
# forbidden in rendered configuration is a public URL, hosted provider record,
# hosted quota class, broker gateway, duplicate assignment path, or autonomous
# mutation flag.
config_forbidden_regex='api\.openrouter\.ai|api\.cerebras\.ai|api\.groq\.com|api\.x\.ai|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.mistral\.ai|workers\.ai|name:[[:space:]]*(openrouter|cerebras|groq|grok|xai)([[:space:]]|$)|provider:[[:space:]]*(openrouter|cerebras|groq|grok|xai)([[:space:]]|$)|quota_class:[[:space:]]*(premium_free|fast_free|edge_free|brokered_free)([[:space:]]|$)|gateway_mode:[[:space:]]*(sidecar|brokered)([[:space:]]|$)|AUTO_ROUTER_(AUTOLOAD_ENABLED|PLACEMENT_UNLOAD|FLEET_DISPATCHER_ENABLED|AGENTGATEWAY_ENABLED)[^:=]*[:=][[:space:]]*[^[:alnum:]]*true([[:space:]]|$)|ASSISTX_OVERLAY_MODE[^:=]*[:=][[:space:]]*[^[:alnum:]]*router_plus_assign|AUTO_ASSIGN_BASE_URL[^:=]*[:=][[:space:]]*[^[:alnum:]]*https?://|HERMES_SELFTASK_ENABLED[^:=]*[:=][[:space:]]*[^[:alnum:]]*true([[:space:]]|$)|ASSISTX_RECOVERY_EXECUTION_ENABLED[^:=]*[:=][[:space:]]*[^[:alnum:]]*true([[:space:]]|$)|FLEET_UNSAFE_SHELL_TASKS_ENABLED[^:=]*[:=][[:space:]]*[^[:alnum:]]*true([[:space:]]|$)'
runtime_forbidden_regex='openrouter|cerebras|groq|grok|xai|anthropic|gemini|mistral|cloudflare'
allowed_doc_regex='docs/|README|archive/|FULL_AUTO_RECONCILIATION|SYSTEM_INVENTORY|system-inventory'
hosted_keys=(OPENROUTER_API_KEY GROQ_API_KEY CEREBRAS_API_KEY XAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY MISTRAL_API_KEY CLOUDFLARE_API_TOKEN)

failures=0
printf '[offline-verify] scanning rendered configuration\n'
for path in "${SCAN_PATHS[@]}"; do
  [ -e "$path" ] || { printf 'missing scan path: %s\n' "$path" >&2; failures=$((failures + 1)); continue; }
  while IFS= read -r match; do
    [ -n "$match" ] || continue
    file="${match%%:*}"
    if printf '%s' "$file" | grep -Eiq "$allowed_doc_regex"; then
      continue
    fi
    printf 'forbidden provider, authority, or mutation configuration: %s\n' "$match" >&2
    failures=$((failures + 1))
  done < <(grep -RInE --exclude-dir=.git --exclude='*.md' --exclude='*.jsonl' --exclude='*.log' "$config_forbidden_regex" "$path" 2>/dev/null || true)
done

# Report non-empty hosted-provider variables from the current process by NAME only.
for name in "${hosted_keys[@]}"; do
  if [ -n "${!name:-}" ]; then
    printf 'hosted-provider variable is non-empty in process environment: %s\n' "$name" >&2
    failures=$((failures + 1))
  fi
done

# Inspect the shadow env file without sourcing it and without printing values.
if [ -n "$ENV_FILE" ]; then
  if [ ! -f "$ENV_FILE" ]; then
    printf 'reconciliation env file not found: %s\n' "$ENV_FILE" >&2
    failures=$((failures + 1))
  else
    for name in "${hosted_keys[@]}"; do
      line="$(grep -E "^[[:space:]]*${name}=" "$ENV_FILE" | tail -n 1 || true)"
      [ -n "$line" ] || continue
      value="${line#*=}"
      value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')"
      if [ -n "$value" ]; then
        printf 'hosted-provider variable is non-empty in reconciliation env file: %s\n' "$name" >&2
        failures=$((failures + 1))
      fi
    done
  fi
fi

printf '[offline-verify] probing reconciliation services\n'
router_health="$(mktemp)"
router_models="$(mktemp)"
assistx_health="$(mktemp)"
trap 'rm -f "$router_health" "$router_models" "$assistx_health"' EXIT

if ! curl -fsS --max-time 5 "${ROUTER_URL%/}/health" >"$router_health"; then
  printf 'router health failed: %s\n' "$ROUTER_URL" >&2
  failures=$((failures + 1))
fi
if ! curl -fsS --max-time 10 "${ROUTER_URL%/}/v1/models" >"$router_models"; then
  printf 'router model listing failed: %s\n' "$ROUTER_URL" >&2
  failures=$((failures + 1))
elif grep -Eiq "$runtime_forbidden_regex" "$router_models"; then
  printf 'router model listing exposes a forbidden provider or model namespace\n' >&2
  failures=$((failures + 1))
fi
if ! curl -fsS --max-time 5 "${ASSISTX_URL%/}/health" >"$assistx_health"; then
  printf 'AssistX health failed: %s\n' "$ASSISTX_URL" >&2
  failures=$((failures + 1))
fi

# Require loopback-facing URLs for the shadow control surfaces.
python3 - "$ROUTER_URL" "$ASSISTX_URL" <<'PY' || failures=$((failures + 1))
import ipaddress
import socket
import sys
from urllib.parse import urlparse

for value in sys.argv[1:]:
    parsed = urlparse(value)
    host = parsed.hostname
    if not host:
        raise SystemExit(f"invalid URL: {value}")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, parsed.port or 80)}
    except OSError as exc:
        raise SystemExit(f"cannot resolve {host}: {exc}")
    if not all(ipaddress.ip_address(addr).is_loopback for addr in addresses):
        raise SystemExit(f"shadow control URL is not loopback-only: {value} -> {sorted(addresses)}")
PY

if [ "$failures" -ne 0 ]; then
  printf '[offline-verify] FAILED with %d finding(s)\n' "$failures" >&2
  exit 1
fi
printf '[offline-verify] PASS\n'
