#!/usr/bin/env bash
# One local, read-only consumption scan; no provider requests or model admission.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS="${ASSISTX_BURN_OUTPUT_DIR:-/home/scott/git/auto-assist-wt-ab3bb64/artifacts}"
TEMPLATES="${ASSISTX_BURN_TEMPLATE_DIR:-/home/scott/git/auto-assist-wt-ab3bb64/templates}"
STATE="${ASSISTX_BURN_STATE_DIR:-/home/scott/.local/state/auto-assist/provider-burn-deploy}"
mkdir -p -m 700 "$STATE"
exec 9>"$STATE/refresh.lock"
flock -n 9 || exit 0

test -d "$ARTIFACTS"
test -d "$TEMPLATES"
REPORT="$ARTIFACTS/free-endpoint-burn.json"
PANEL="$TEMPLATES/provider_burn_panel.html"
TEMP="$(mktemp "$ARTIFACTS/.provider-burn-XXXXXXXX.json")"
trap 'rm -f "$TEMP"' EXIT
PYTHONPATH="$ROOT/src" /usr/bin/python3 "$ROOT/scripts/free_endpoint_burn_dashboard.py" --out-json "$TEMP"
chmod 0600 "$TEMP"
mv -f "$TEMP" "$REPORT"
PYTHONPATH="$ROOT/src" /usr/bin/python3 "$ROOT/scripts/render_provider_burn_fragment.py" --report "$REPORT" --out "$PANEL" --history "$STATE/observations.jsonl"

# Durable, append-only compact observation ledger (not the raw per-model export).
/usr/bin/python3 - "$REPORT" "$PANEL" "$STATE/observations.jsonl" <<'PY'
import hashlib,json,os,sys,time
from pathlib import Path
report,panel,ledger=map(Path,sys.argv[1:4])
data=json.loads(report.read_text(encoding="utf-8"))
providers=[
 {"provider":v.get("provider"),"last_24h":v.get("observed_tokens_24h"),
  "last_7d":v.get("observed_tokens_7d")}
 for v in data.get("by_provider",[]) if isinstance(v,dict)
]
record={
 "schema":"fleet-provider-burn-audit/v1",
 "observed_at_epoch":time.time(),"source_generated_epoch":data.get("generated_epoch"),
 "step_events":data.get("step_events"),"complete":data.get("complete") is True,
 "providers":providers,
 "report_sha256":hashlib.sha256(report.read_bytes()).hexdigest(),
 "panel_sha256":hashlib.sha256(panel.read_bytes()).hexdigest(),
}
line=(json.dumps(record,sort_keys=True,separators=(",",":"))+"\n").encode()
fd=os.open(ledger,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
try:
 os.write(fd,line)
 os.fsync(fd)
finally:
 os.close(fd)
print("observation_recorded",len(providers),"providers")
PY
