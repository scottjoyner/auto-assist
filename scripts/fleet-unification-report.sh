#!/usr/bin/env bash
set -euo pipefail

DASH="/home/scott/knowledge/60-Mappings/Fleet-Status-Dashboard.md"
REPORT_ROOT="/nas/fileserver/fleet-reports"
STATE_DIR="${HOME}/.hermes/state"
STATE_FILE="${STATE_DIR}/fleet-unification-notification.json"
VERIFY_LOG="$(mktemp /tmp/fleet-unification-report.XXXXXX.log)"
trap 'rm -f "$VERIFY_LOG"' EXIT

if ! dashboard_path=$(/home/scott/bin/verify-fleet-unification 2>"$VERIFY_LOG"); then
    printf 'Fleet regression: daily evidence generation FAILED.\n'
    sed -n '1,3p' "$VERIFY_LOG"
    exit 1
fi

python3 - "$DASH" "$REPORT_ROOT" "$STATE_FILE" "$dashboard_path" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

_, dashboard_name, report_root, state_file, dashboard_path = sys.argv
text = Path(dashboard_name).read_text(encoding="utf-8")

state = {}
try:
    state = json.loads(Path(state_file).read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError, OSError):
    state = {}

current = {}
for service in ("auto-assist", "auto-assign", "auto-ingest", "auto-router"):
    m = re.search(rf"- `{re.escape(service)}`: `([^`]+)`", text)
    current[service] = m.group(1) if m else "NOT_PROBED"

m = re.search(r"- `/nas` health: `([^`]+)`", text)
current["nas_health"] = m.group(1) if m else "UNKNOWN"
m = re.search(r"- `AGENT_FLEET`: ([^\n]+)", text)
current["agent_fleet"] = m.group(1).strip() if m else "UNKNOWN"

previous = state.get("current", {})
changes = []
for key in (*["auto-assist", "auto-assign", "auto-ingest", "auto-router"], "nas_health", "agent_fleet"):
    if previous.get(key) != current[key]:
        old = previous.get(key, "UNSEEN")
        changes.append(f"{key}: {old} -> {current[key]}")

Path(state_file).parent.mkdir(parents=True, exist_ok=True)
Path(state_file).write_text(json.dumps({"current": current}, indent=2, sort_keys=True) + "\n")

if not changes:
    # Empty stdout is intentional: collection continues, Signal delivery is silent.
    raise SystemExit(0)

print("Fleet state transition: " + "; ".join(changes))
print(f"Full report: {report_root}")
print(f"Dashboard: {dashboard_path}")
PY
