#!/usr/bin/env bash
set -euo pipefail

EXPECTED_HOST="${EXPECTED_HOST:-x1-370}"
OPS_ROOT="${OPS_ROOT:-$(git rev-parse --show-toplevel)}"
RUNTIME_ENV="${RUNTIME_ENV:-$OPS_ROOT/.canary-runtime.env}"
LATEST_FILE="$OPS_ROOT/.latest-replica-canary-dir"

if [[ "$(hostname -s)" != "$EXPECTED_HOST" ]]; then
  echo "refusing after capture on $(hostname -s); expected $EXPECTED_HOST" >&2
  exit 2
fi

test -r "$RUNTIME_ENV"
test -r "$LATEST_FILE"
# shellcheck disable=SC1090
source "$RUNTIME_ENV"

canary_dir="$(cat "$LATEST_FILE")"
test -d "$canary_dir"
test -r "$canary_dir/capture-state.json"
test ! -e "$canary_dir/result.json"

for cmd in docker python3 base64; do
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

python3 "$OPS_ROOT/scripts/capture_mobile_model_replica_canary.py" after   --assistx-base-url "$assistx_base_url"   --router-db "$router_db"   --assistx-sha "$ASSISTX_RUNTIME_SHA"   --router-sha "$AUTO_ROUTER_RUNTIME_SHA"   --out-dir "$canary_dir"   --header-env "Authorization=KIPNERTER_CANARY_AUTH"   | tee "$canary_dir/after-summary.json"

echo
echo "FINAL RESULT"
cat "$canary_dir/result.json"
echo
echo "Evidence retained at: $canary_dir"
