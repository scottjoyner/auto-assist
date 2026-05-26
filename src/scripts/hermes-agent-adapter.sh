#!/usr/bin/env bash
# Wrapper for systemd hermes-agent-adapter.service
# Installed at ~/.local/bin/hermes-agent-adapter (copied by user)
set -euo pipefail
cd /home/scott/git/auto-assist
exec .venv/bin/python -m assistx.agents.hermes_agent_adapter "$@"