#!/usr/bin/env bash
set -euo pipefail

EXPECTED_HOST="${EXPECTED_HOST:-x1-370}"
OPS_ROOT="${OPS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUNTIME_ENV="${RUNTIME_ENV:-$OPS_ROOT/.canary-runtime.env}"
EVIDENCE_ROOT="${CANARY_EVIDENCE_ROOT:-$HOME/git/canary-evidence}"

if [[ "$(hostname -s)" != "$EXPECTED_HOST" ]]; then
  echo "refusing before capture on $(hostname -s); expected $EXPECTED_HOST" >&2
  exit 2
fi

test -r "$RUNTIME_ENV"
# shellcheck disable=SC1090
source "$RUNTIME_ENV"

for cmd in docker curl python3 base64; do
  command -v "$cmd" >/dev/null || {
    echo "missing required command: $cmd" >&2
    exit 2
  }
done

assistx_container="$(
  docker ps --format '{{.Names}}' |
    grep -E '(^|[-_])assistx-api($|[-_])|^assistx-api$' |
    head -n1 || true
)"
router_container="$(
  docker ps --format '{{.Names}}' |
    grep -E '(^|[-_])auto-router($|[-_])|^auto-router$' |
    head -n1 || true
)"
test -n "$assistx_container"
test -n "$router_container"

readarray -t auth_values < <(
  docker inspect "$assistx_container" |
    python3 -c '
import json,sys
obj=json.load(sys.stdin)[0]
env={}
for item in obj.get("Config",{}).get("Env",[]) or []:
    if "=" in item:
        key,value=item.split("=",1)
        env[key]=value
print(env.get("BASIC_AUTH_USER",""))
print(env.get("BASIC_AUTH_PASS",""))
'
)
basic_user="${auth_values[0]:-}"
basic_pass="${auth_values[1]:-}"
test -n "$basic_user"
test -n "$basic_pass"

export KIPNERTER_CANARY_AUTH="Basic $(printf '%s:%s' "$basic_user" "$basic_pass" | base64 -w0)"

port_line="$(docker port "$assistx_container" 8000/tcp | head -n1)"
test -n "$port_line"
assistx_port="${port_line##*:}"
[[ "$assistx_port" =~ ^[0-9]+$ ]]
assistx_base_url="http://127.0.0.1:$assistx_port"

router_data_source="$(
  docker inspect "$router_container" |
    python3 -c '
import json,sys
obj=json.load(sys.stdin)[0]
matches=[m.get("Source") for m in obj.get("Mounts",[]) if m.get("Destination")=="/data"]
print(matches[0] if len(matches)==1 else "")
'
)"
test -n "$router_data_source"
router_db="${router_data_source%/}/router.sqlite3"
test -r "$router_db"

tmp_catalog="$(mktemp)"
trap 'rm -f "$tmp_catalog"' EXIT
curl -fsS   -H "Authorization: $KIPNERTER_CANARY_AUTH"   "$assistx_base_url/api/v1/runtime/catalog"   > "$tmp_catalog"

selected="$(
  python3 - "$tmp_catalog" "${DISPLAY_NAME:-}" <<'PY'
import json,sys
path, requested = sys.argv[1], sys.argv[2].strip().lower()
catalog=json.load(open(path))
rows=[]
for row in catalog.get("models",[]):
    if not isinstance(row,dict) or row.get("state")!="ready":
        continue
    try:
        count=int(row.get("ready_runtime_count") or 0)
    except (TypeError,ValueError):
        continue
    if count < 2:
        continue
    handle=row.get("model_handle")
    name=str(row.get("display_name") or "")
    if not isinstance(handle,str) or not handle.startswith("model:v1:"):
        continue
    rows.append((name,handle,count))
if requested:
    exact=[row for row in rows if row[0].lower()==requested]
    if len(exact)!=1:
        raise SystemExit(f"requested replicated model {requested!r} not found uniquely")
    chosen=exact[0]
else:
    def score(row):
        name=row[0].lower()
        if "ternary bonsai" in name:
            priority=0
        elif "bonsai" in name:
            priority=1
        elif "k2" in name:
            priority=2
        else:
            priority=3
        return (priority,-row[2],name,row[1])
    if not rows:
        raise SystemExit("no ready replicated Fleet model is available")
    chosen=sorted(rows,key=score)[0]
print(json.dumps({"display_name":chosen[0],"model_handle":chosen[1],"ready_runtime_count":chosen[2]}))
PY
)"
handle="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["model_handle"])' "$selected")"
display_name="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["display_name"])' "$selected")"

mkdir -p "$EVIDENCE_ROOT"
canary_dir="${CANARY_DIR:-$EVIDENCE_ROOT/kipnerter-replica-$(date -u +%Y%m%dT%H%M%SZ)}"

echo "Selected replicated Fleet model: $display_name"
echo "Evidence directory: $canary_dir"

python3 "$OPS_ROOT/scripts/capture_mobile_model_replica_canary.py" before   --assistx-base-url "$assistx_base_url"   --router-db "$router_db"   --handle "$handle"   --assistx-sha "$ASSISTX_RUNTIME_SHA"   --router-sha "$AUTO_ROUTER_RUNTIME_SHA"   --out-dir "$canary_dir"   --header-env "Authorization=KIPNERTER_CANARY_AUTH"   | tee "$canary_dir-before-summary.tmp"

mkdir -p "$canary_dir"
mv "$canary_dir-before-summary.tmp" "$canary_dir/before-summary.json"
printf '%s\n' "$canary_dir" > "$OPS_ROOT/.latest-replica-canary-dir"

target="$(
  python3 - "$canary_dir/before-summary.json" <<'PY'
import json,sys
data=json.load(open(sys.argv[1]))
print(data.get("transition_target_provider") or "")
PY
)"
test -n "$target"

cat <<EOF

BEFORE CAPTURE COMPLETE

Model: $display_name
Handle: $handle
Transition target provider: $target
Evidence: $canary_dir

STOP HERE until the exact provider above is removed from eligibility through
the approved runtime/canary procedure. Do not remove an arbitrary sibling.

After the catalog shows a smaller positive replica count, run:

  cd "$OPS_ROOT" && bash scripts/run-x1-370-fleet-canary-after.sh

EOF
