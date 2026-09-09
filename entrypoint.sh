#!/bin/bash
set -e

# Initialize /app/hermes-home from baked-in defaults if volume is empty.
HERMES_HOME="${HERMES_HOME:-/app/hermes-home}"
DEFAULTS="/app/hermes-home.defaults"

mkdir -p "${HERMES_HOME}"
if [ ! -f "${HERMES_HOME}/config.yaml" ] && [ -f "${DEFAULTS}/config.yaml" ]; then
    echo "Initializing ${HERMES_HOME} from defaults..."
    cp "${DEFAULTS}/config.yaml" "${HERMES_HOME}/config.yaml"
fi

# A missing config makes provider selection fail as "Unknown provider". Fail
# closed instead of allowing the API to start with a dangling Hermes home.
test -f "${HERMES_HOME}/config.yaml"

# Ensure ~/.hermes/config.yaml exists
mkdir -p /root/.hermes
if [ ! -f /root/.hermes/config.yaml ]; then
    ln -sf "${HERMES_HOME}/config.yaml" /root/.hermes/config.yaml 2>/dev/null || \
    cp "${HERMES_HOME}/config.yaml" /root/.hermes/config.yaml 2>/dev/null || true
fi

# Ensure required subdirs exist
mkdir -p "${HERMES_HOME}"/{sessions,logs,cache,harvest,memories,profiles,tmp}

exec "$@"
