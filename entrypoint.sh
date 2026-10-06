#!/bin/bash
set -e

# Initialize /app/hermes-home from baked-in defaults if volume is empty.
HERMES_HOME="${HERMES_HOME:-/app/hermes-home}"
DEFAULTS="/app/hermes-home.defaults"

if [ ! -f "${HERMES_HOME}/config.yaml" ] && [ -d "$DEFAULTS" ]; then
    echo "Initializing ${HERMES_HOME} from defaults..."
    cp -r "$DEFAULTS"/* "$HERMES_HOME/" 2>/dev/null || true
fi

# Ensure ~/.hermes/config.yaml exists
mkdir -p /root/.hermes
if [ ! -f /root/.hermes/config.yaml ]; then
    ln -sf "${HERMES_HOME}/config.yaml" /root/.hermes/config.yaml 2>/dev/null || \
    cp "${HERMES_HOME}/config.yaml" /root/.hermes/config.yaml 2>/dev/null || true
fi

# Merge-safe credential bake (do NOT overwrite other fleet agents' keys).
# FLEET_ROUTER_BEARER_TOKEN is supplied by compose env_file (injected into this
# container's os.environ). Seed ~/.hermes/.env from the current environment only
# when no shared .env already exists, so xwing and any co-resident hermes agent's
# credentials are preserved verbatim. Then ensure THIS token key is present in
# either case (additive append when missing) without touching other keys.
ENV_SEED="${ENV_SEED:-/root/.hermes/.env}"
if [ ! -f "${ENV_SEED}" ]; then
    env | grep -E "^[A-Za-z_][A-Za-z0-9_]*=" > "${ENV_SEED}" 2>/dev/null || true
fi
if [ -n "${FLEET_ROUTER_BEARER_TOKEN:-}" ] && [ -f "${ENV_SEED}" ]; then
    if ! grep -q "^FLEET_ROUTER_BEARER_TOKEN="${ENV_SEED} 2>/dev/null; then
        printf 'FLEET_ROUTER_BEARER_TOKEN=%s\n' "${FLEET_ROUTER_BEARER_TOKEN}" >> "${ENV_SEED}"
    fi
fi

# Ensure required subdirs exist
mkdir -p "${HERMES_HOME}"/{sessions,logs,cache,harvest,memories,profiles,tmp}

exec "$@"
