#!/usr/bin/env bash
set -euo pipefail

EXPECTED_HOST="${EXPECTED_HOST:-x1-370}"
OPS_ROOT="${OPS_ROOT:-$HOME/git/auto-assist-canary-ops}"
RUNTIME_ENV="${RUNTIME_ENV:-$OPS_ROOT/.canary-runtime.env}"
PREFLIGHT_OUT_DIR="${PREFLIGHT_OUT_DIR:-$HOME/git/canary-evidence/live-fleet-preflight-$(date -u +%Y%m%dT%H%M%SZ)}"

if [[ "$(hostname -s)" != "$EXPECTED_HOST" ]]; then
  echo "refusing live preflight on $(hostname -s); expected $EXPECTED_HOST" >&2
  exit 2
fi

test -r "$RUNTIME_ENV"
# shellcheck disable=SC1090
source "$RUNTIME_ENV"

for cmd in tailscale docker curl python3 git sha256sum; do
  command -v "$cmd" >/dev/null || {
    echo "missing required command: $cmd" >&2
    exit 2
  }
done

for pair in   "$ASSISTX_RUNTIME_WORKTREE:$ASSISTX_RUNTIME_SHA"   "$AUTO_ROUTER_RUNTIME_WORKTREE:$AUTO_ROUTER_RUNTIME_SHA"
do
  path="${pair%%:*}"
  sha="${pair##*:}"
  git -C "$path" rev-parse --is-inside-work-tree >/dev/null
  test "$(git -C "$path" rev-parse HEAD)" = "$sha"
  test -z "$(git -C "$path" status --porcelain)"
done

mkdir -p "$PREFLIGHT_OUT_DIR"

tailscale status --json > "$PREFLIGHT_OUT_DIR/tailscale-status.json"
python3 - "$PREFLIGHT_OUT_DIR/tailscale-status.json" <<'PY'
import json,sys
data=json.load(open(sys.argv[1]))
self_node=data.get("Self") or {}
dns=(self_node.get("DNSName") or "").rstrip(".")
if not dns:
    raise SystemExit("tailscale Self.DNSName missing")
print(f"Tailscale node: {dns}")
PY

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

echo "$assistx_container" > "$PREFLIGHT_OUT_DIR/assistx-container.txt"
echo "$router_container" > "$PREFLIGHT_OUT_DIR/auto-router-container.txt"

local_assistx_hash="$(
  sha256sum "$ASSISTX_RUNTIME_WORKTREE/src/assistx/mobile_agent_routes.py" |
    awk '{print $1}'
)"
live_assistx_hash="$(
  docker exec "$assistx_container" python -c     'import hashlib; print(hashlib.sha256(open("/app/src/assistx/mobile_agent_routes.py","rb").read()).hexdigest())'
)"
test "$local_assistx_hash" = "$live_assistx_hash"
printf '%s\n' "$live_assistx_hash" > "$PREFLIGHT_OUT_DIR/assistx-mobile-route.sha256"

router_pkg="$(
  docker exec "$router_container" python -c     'import auto_router,pathlib; print(pathlib.Path(auto_router.__file__).resolve().parent)'
)"
: > "$PREFLIGHT_OUT_DIR/auto-router-authority-hashes.txt"
for rel in policy.py route_events.py; do
  expected="$(
    sha256sum "$AUTO_ROUTER_RUNTIME_WORKTREE/src/auto_router/$rel" |
      awk '{print $1}'
  )"
  actual="$(
    docker exec "$router_container" python -c       "import hashlib; print(hashlib.sha256(open('$router_pkg/$rel','rb').read()).hexdigest())"
  )"
  test "$expected" = "$actual"
  printf '%s %s\n' "$rel" "$actual"     >> "$PREFLIGHT_OUT_DIR/auto-router-authority-hashes.txt"
done

file_mtime="$(
  docker exec "$assistx_container" stat -c %Y /app/src/assistx/mobile_agent_routes.py
)"
container_started="$(
  docker inspect --format '{{.State.StartedAt}}' "$assistx_container"
)"
python3 - "$file_mtime" "$container_started" <<'PY'
import datetime,sys
mtime=int(sys.argv[1])
started=datetime.datetime.fromisoformat(sys.argv[2].replace("Z","+00:00")).timestamp()
if started < mtime:
    raise SystemExit(
        "assistx-api started before current mobile_agent_routes.py mtime; "
        "restart/redeploy is required for exact runtime proof"
    )
PY

docker exec "$assistx_container" sh -lc   'curl -fsS -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" http://127.0.0.1:8000/api/v1/runtime/catalog'   > "$PREFLIGHT_OUT_DIR/runtime-catalog.json"

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
echo "$router_db" > "$PREFLIGHT_OUT_DIR/router-db-path.txt"

python3 - "$PREFLIGHT_OUT_DIR" <<'PY'
import json,os,sys
from pathlib import Path

out=Path(sys.argv[1])
catalog=json.loads((out/"runtime-catalog.json").read_text())
models=catalog.get("models") if isinstance(catalog,dict) else None
if not isinstance(models,list):
    raise SystemExit("runtime catalog missing models")

replicated=[]
for row in models:
    if not isinstance(row,dict) or row.get("state")!="ready":
        continue
    try:
        count=int(row.get("ready_runtime_count") or 0)
    except (TypeError,ValueError):
        continue
    if count < 2:
        continue
    replicated.append({
        "model_handle": row.get("model_handle"),
        "display_name": row.get("display_name"),
        "ready_runtime_count": count,
        "state": row.get("state"),
    })

summary={
    "host": os.uname().nodename,
    "assistx_runtime_sha": os.environ["ASSISTX_RUNTIME_SHA"],
    "auto_router_runtime_sha": os.environ["AUTO_ROUTER_RUNTIME_SHA"],
    "replicated_ready_models": replicated,
    "replicated_ready_model_count": len(replicated),
    "preflight": "PASS" if replicated else "NO_REPLICATED_READY_MODEL",
}
(out/"summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
if not replicated:
    raise SystemExit("no ready mobile catalog model has >=2 replicas")
print(json.dumps(summary,indent=2,sort_keys=True))
PY

echo
echo "LIVE PREFLIGHT PASS"
echo "Evidence: $PREFLIGHT_OUT_DIR"
