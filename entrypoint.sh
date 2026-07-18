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

# Ensure required subdirs exist
mkdir -p "${HERMES_HOME}"/{sessions,logs,cache,harvest,memories,profiles,tmp}

exec "$@"
