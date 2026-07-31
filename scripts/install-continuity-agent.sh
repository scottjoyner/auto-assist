#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 2
fi

MODE=${1:-}
if [[ ${MODE} != "node" && ${MODE} != "replicator" ]]; then
  echo "Usage: $0 node|replicator" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_DIR=${SOURCE_DIR:-${ROOT}}
SERVICE_USER=${SERVICE_USER:-assistx-continuity}
SERVICE_GROUP=${SERVICE_GROUP:-assistx-continuity}
INSTALL_ROOT=${INSTALL_ROOT:-/srv/assistx-continuity}
VENV_DIR=${VENV_DIR:-${INSTALL_ROOT}/venv}
CONFIG_DIR=${CONFIG_DIR:-/etc/assistx-continuity}
STATE_DIR=${STATE_DIR:-/var/lib/assistx-continuity}

if [[ ! -f ${SOURCE_DIR}/src/assistx/continuity_node_agent.py ]] || \
   [[ ! -f ${SOURCE_DIR}/src/assistx/continuity_replicator.py ]]; then
  echo "SOURCE_DIR must contain the reviewed continuity branch source." >&2
  exit 2
fi
if ! command -v python3 >/dev/null; then
  echo "python3 is required." >&2
  exit 2
fi

if ! getent group "${SERVICE_GROUP}" >/dev/null; then
  groupadd --system "${SERVICE_GROUP}"
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --gid "${SERVICE_GROUP}" \
    --home-dir "${INSTALL_ROOT}" \
    --create-home \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}"
fi

install -d -m 0750 -o root -g "${SERVICE_GROUP}" \
  "${INSTALL_ROOT}" \
  "${INSTALL_ROOT}/docs" \
  "${CONFIG_DIR}"
install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
  "${STATE_DIR}"

install -m 0640 -o root -g "${SERVICE_GROUP}" \
  "${SOURCE_DIR}/docs/CONTINUITY_AGENTS_20260731.md" \
  "${INSTALL_ROOT}/docs/CONTINUITY_AGENTS_20260731.md"

if [[ ! -x ${VENV_DIR}/bin/python ]]; then
  runuser -u "${SERVICE_USER}" -- python3 -m venv "${VENV_DIR}"
fi
runuser -u "${SERVICE_USER}" -- \
  "${VENV_DIR}/bin/python" -m pip install \
    --no-deps \
    --no-build-isolation \
    -e "${SOURCE_DIR}"

if [[ ${MODE} == "node" ]]; then
  TEMPLATE="${SOURCE_DIR}/deploy/reconciliation/continuity-node.env.example"
  ENV_FILE="${CONFIG_DIR}/node.env"
  UNIT_SOURCE="${SOURCE_DIR}/deploy/reconciliation/systemd/assistx-continuity-node.service"
  UNIT_NAME=assistx-continuity-node.service
else
  TEMPLATE="${SOURCE_DIR}/deploy/reconciliation/continuity-replicator.env.example"
  ENV_FILE="${CONFIG_DIR}/replicator.env"
  UNIT_SOURCE="${SOURCE_DIR}/deploy/reconciliation/systemd/assistx-continuity-replicator.service"
  UNIT_NAME=assistx-continuity-replicator.service
fi

if [[ ! -f ${ENV_FILE} ]]; then
  install -m 0600 -o root -g "${SERVICE_GROUP}" \
    "${TEMPLATE}" "${ENV_FILE}"
fi
if [[ ! -f ${CONFIG_DIR}/continuity-token ]]; then
  install -m 0600 -o root -g "${SERVICE_GROUP}" /dev/null \
    "${CONFIG_DIR}/continuity-token"
fi
if [[ ${MODE} == "replicator" && ! -f ${CONFIG_DIR}/source-basic-password ]]; then
  install -m 0600 -o root -g "${SERVICE_GROUP}" /dev/null \
    "${CONFIG_DIR}/source-basic-password"
fi

install -m 0644 "${UNIT_SOURCE}" "/etc/systemd/system/${UNIT_NAME}"
systemctl daemon-reload

if [[ ! -s ${CONFIG_DIR}/continuity-token ]]; then
  echo "Installed ${UNIT_NAME}, but the continuity token file is empty." >&2
  echo "Populate ${CONFIG_DIR}/continuity-token before enabling the service." >&2
  exit 0
fi
if grep -E -q 'replace-with|192\.168\.1\.20|100\.64\.0\.20|^FLEET_NODE_ID=xwing$' "${ENV_FILE}"; then
  echo "Installed ${UNIT_NAME}, but ${ENV_FILE} still contains example values." >&2
  echo "Review the node/controller identities, LAN/Tailscale URLs, and allowlists." >&2
  exit 0
fi
if [[ ${MODE} == "replicator" && ! -s ${CONFIG_DIR}/source-basic-password ]]; then
  echo "Populate ${CONFIG_DIR}/source-basic-password before enabling the replicator." >&2
  exit 0
fi

systemctl enable --now "${UNIT_NAME}"
systemctl --no-pager --full status "${UNIT_NAME}" || true
