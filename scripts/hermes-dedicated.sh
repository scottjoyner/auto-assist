#!/usr/bin/env bash
# hermes-dedicated.sh — launch a Hermes session on a dedicated fleet node so
# background swarm work (code-iteration loop, self-tasks) never clobbers it.
#
# What it does:
#   1. Reserves <node>:1234 in the fleet reservation lock (fleet_reserve.py)
#      so the AssistX hermes-adapter's select_any() skips that node for all
#      other tasks until the session ends (or the TTL expires as a safety net).
#   2. Runs your Hermes command (everything after --).
#   3. On exit (Ctrl-C, crash, normal) it RELEASES the reservation.
#
# Usage:
#   hermes-dedicated.sh <node> <minutes> -- <hermes command...>
#   hermes-dedicated.sh x1-370 240 -- hermes chat --model 'lmstudio-x1-370/ornith-1.0-35b' --toolsets terminal,file,skills,web
#
# If your hermes invocation takes --toolsets, pass it after -- so the session
# has the right tools for the job (code work => terminal,file,skills,web).
#
# Requires: fleet_reserve.py on PATH (or alongside this script).

set -u

NODE="${1:-x1-370}"
MINUTES="${2:-240}"
shift 2 || true

if [ "$#" -lt 1 ] || [ "${1:-}" != "--" ]; then
  echo "Usage: $0 <node> <minutes> -- <hermes command...>" >&2
  exit 2
fi
shift # drop the --

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESERVE="$SCRIPT_DIR/fleet_reserve.py"

# Find fleet_reserve.py: alongside this script, or on PATH
if [ ! -x "$RESERVE" ] && ! command -v fleet_reserve.py >/dev/null 2>&1; then
  echo "ERROR: fleet_reserve.py not found next to this script or on PATH" >&2
  exit 3
fi
[ -x "$RESERVE" ] || RESERVE="fleet_reserve.py"

cleanup() {
  echo "" >&2
  echo "[hermes-dedicated] session ended — releasing reservation on $NODE" >&2
  python3 "$RESERVE" release "$NODE" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "[hermes-dedicated] reserving $NODE:1234 for ${MINUTES}m (background fleet work excluded)" >&2
python3 "$RESERVE" reserve "$NODE" --minutes "$MINUTES" --by "hermes-dedicated-$$" --purpose "interactive hermes session"
if [ $? -ne 0 ]; then
  echo "WARNING: failed to set reservation; session may contend with fleet work" >&2
fi

echo "[hermes-dedicated] launching: $*" >&2
"$@"
