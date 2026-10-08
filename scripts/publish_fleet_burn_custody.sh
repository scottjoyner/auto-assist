#!/usr/bin/env bash
# Read-only NAS manifest inventory; publish private custody, never inference.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${FLEET_CUSTODY_STATE:-/home/scott/.local/state/auto-assist/fleet-burn-custody}"
TEMPLATES="${FLEET_CUSTODY_TEMPLATES:-/home/scott/git/auto-assist-wt-ab3bb64/templates}"
NAS_ROOT="${FLEET_CUSTODY_ARCHIVE_ROOT:-/nas/opencode-fleet}"
mkdir -p -m 700 "$STATE"
test -d "$TEMPLATES"
# Refuse a plain-directory or wrong NAS mount posing as the harvest volume.
if [ "$NAS_ROOT" = /nas/opencode-fleet ]; then
    source_type="$(findmnt -T "$NAS_ROOT" -n -o FSTYPE 2>/dev/null | tail -n1 || true)"
    source_name="$(findmnt -T "$NAS_ROOT" -n -o SOURCE 2>/dev/null | tail -n1 || true)"
    [ "$source_type" = cifs ] || { echo 'BLOCKED: harvest NAS is not CIFS-mounted' >&2; exit 3; }
    [ "$source_name" = '//192.168.1.202/fileserver' ] || { echo 'BLOCKED: harvest NAS source identity changed' >&2; exit 3; }
fi
exec 9>"$STATE/publisher.lock"
flock -n 9 || exit 0
TMP="$(mktemp "$STATE/.inventory-XXXXXXXX.json")"
PANEL_TMP="$(mktemp "$TEMPLATES/.fleet-custody-XXXXXXXX.html")"
trap 'rm -f "$TMP" "$PANEL_TMP"' EXIT
timeout --signal=TERM 45s /usr/bin/python3 "$ROOT/scripts/fleet_burn_custody.py" --root "$NAS_ROOT" --out "$TMP"
PYTHONPATH="$ROOT/scripts" /usr/bin/python3 - "$TMP" "$PANEL_TMP" <<'PY'
import json,sys
from pathlib import Path
from render_fleet_burn_custody import render
from fleet_burn_custody import SCHEMA
raw=json.loads(Path(sys.argv[1]).read_text())
assert raw["schema"]==SCHEMA
Path(sys.argv[2]).write_text(render(raw),encoding="utf-8")
PY
chmod 0600 "$TMP" "$PANEL_TMP"
mv -f "$TMP" "$STATE/latest.json"
mv -f "$PANEL_TMP" "$TEMPLATES/provider_fleet_custody_panel.html"
PYTHONPATH="$ROOT/scripts" /usr/bin/python3 - "$STATE/latest.json" "$TEMPLATES/provider_fleet_custody_panel.html" "$STATE/receipts.jsonl" <<'PY'
import sys
from fleet_custody_chain import append_receipt
record=append_receipt(*sys.argv[1:])
print("custody_receipt_recorded",record["sequence"],record["chain_sha256"][:16])
PY
