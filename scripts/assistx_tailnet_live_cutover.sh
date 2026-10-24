#!/usr/bin/env bash
# Explicitly opt-in controlled cutover. Fail CLOSED or fall back to pinned Caddy.
set -Eeuo pipefail
if [[ "${1:-}" != "--execute-live" || $# -ne 1 ]]; then
  echo 'INERT: pass --execute-live only after the reviewed preflight' >&2
  exit 0
fi
readonly ROOT=/home/scott/.config/assistx-tailnet-sanitizer
readonly WORK=/home/scott/git/wt-assistx-tailnet-boot-fence-20261023
readonly OLD=control-room-ts-proxy.service
readonly PRIMARY=assistx-tailnet-sanitizer.service
readonly FALLBACK=assistx-tailnet-sanitizer-fallback.service
readonly PIN=c4136364469a45f918c8e9a33ce5268f57dc4ee39b030012af4f66aaf9b186b2
cutover_started=0
fail_closed_recovery() {
  local rc=$?
  trap - ERR
  if [[ "$cutover_started" -eq 1 ]]; then
    echo 'PRIMARY CHECK FAILED: attempting pinned Caddy-only fallback; NEVER socat' >&2
    systemctl --user disable --now "$PRIMARY" >/dev/null 2>&1 || true
    if [[ "$(systemctl --user is-active "$OLD" 2>/dev/null || true)" == active ]]; then
      echo 'UNSAFE: legacy service unexpectedly active; fallback held' >&2
    elif ! systemctl --user enable --now "$FALLBACK" >/dev/null 2>&1; then
      echo 'RECOVERY FAILED: remain fail-closed; DO NOT restore socat' >&2
    else
      echo 'SANITIZING_CADDY_FALLBACK_ACTIVE: inspect independently before client use' >&2
    fi
  fi
  exit "$rc"
}
trap fail_closed_recovery ERR
[[ "$(hostname)" == x1-370 && "$(id -u)" == 1000 ]]
[[ "$(tailscale ip -4)" == 100.64.43.123 ]]
git -C "$WORK" merge-base --is-ancestor 42132145fb49742142a94cfdaf20f851c1686805 HEAD
[[ -z "$(git -C "$WORK" status --porcelain)" ]]
[[ "$(systemctl --user is-active "$OLD")" == active ]]
[[ "$(systemctl --user is-enabled "$OLD")" == enabled ]]
[[ "$(systemctl --user is-active "$PRIMARY" 2>/dev/null || true)" != active ]]
[[ "$(systemctl --user is-active "$FALLBACK" 2>/dev/null || true)" != active ]]
[[ "$(systemctl --user is-enabled "$PRIMARY")" == disabled ]]
[[ "$(systemctl --user is-enabled "$FALLBACK")" == disabled ]]
sha256sum --status --check "$ROOT/caddy-binary.sha256"
sha256sum --status --check "$ROOT/Caddyfile.sha256"
sha256sum --status --check "$ROOT/fallback.Caddyfile.sha256"
for file in Caddyfile fallback.Caddyfile; do
  [[ "$(sha256sum "$ROOT/$file" | cut -d' ' -f1)" == "$PIN" ]]
  ASSISTX_TAILNET_IP=100.64.43.123 /usr/bin/caddy validate --config "$ROOT/$file" --adapter caddyfile >/dev/null 2>&1
  env -u ASSISTX_TAILNET_IP /usr/bin/caddy adapt --config "$ROOT/$file" --adapter caddyfile 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);assert [x.get("listen") for x in d["apps"]["http"]["servers"].values()]==[["100.64.43.123:8000"]]'
done
[[ "$(curl -sf --max-time 4 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health)" == 200 ]]
PYTHONDONTWRITEBYTECODE=1 python3 -B "$WORK/scripts/assistx_tailnet_sanitizer_preflight.py" --physical-readonly | python3 -c 'import sys,json;j=json.load(sys.stdin);assert j["decision"]=="HOLD" and j["caddy_explicit_tailnet_only_listener_with_env_set_and_unset"] and j["real_caddy_syntax_validated"]'
echo PRECHECKS_PASSED
# Changing boot state BEFORE cutting traffic avoids a reboot-time return to socat.
systemctl --user disable "$OLD" >/dev/null
[[ "$(systemctl --user is-enabled "$OLD")" == disabled ]]
cutover_started=1
systemctl --user stop "$OLD"
[[ "$(systemctl --user is-active "$OLD" 2>/dev/null || true)" != active ]]
systemctl --user enable --now "$PRIMARY" >/dev/null
sleep 2
[[ "$(systemctl --user is-active "$PRIMARY")" == active ]]
[[ "$(systemctl --user is-enabled "$PRIMARY")" == enabled ]]
[[ "$(systemctl --user is-enabled "$OLD")" == disabled ]]
PYTHONDONTWRITEBYTECODE=1 python3 -B "$WORK/scripts/assistx_tailnet_postcutover_attestation.py" --physical-readonly | python3 -c 'import json,sys;x=json.load(sys.stdin);assert x["postcutover_static_gate_met"] and x["tailnet_socket_owned_by_caddy"] and x["pinned_caddy_config_verified"] and x["mobile_serve_scope_exact"] and x["decision"]=="HOLD"'
PYTHONDONTWRITEBYTECODE=1 python3 -B - "$WORK" <<'PY'
import json,subprocess,sys,urllib.error,urllib.request
sys.path.insert(0,sys.argv[1]+"/tests")
from assistx_tailnet_real_basic_canary import get_basic_in_memory
record=json.loads(subprocess.run(['docker','inspect','-f','{{json .}}','assistx-api'],capture_output=True,text=True,check=True).stdout)
basic=get_basic_in_memory(record)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def status(headers):
 request=urllib.request.Request('http://100.64.43.123:8000/control-room',headers=headers,method='GET')
 try:
  with opener.open(request,timeout=5) as response:return response.status
 except urllib.error.HTTPError as exc:
  n=exc.code;exc.close();return n
assert status({})==401
assert status({'Tailscale-User-Login':'synthetic-untrusted-identity'})==401
assert status({'Authorization':basic})==200
assert status({'Authorization':basic,'Tailscale-User-Login':'synthetic-untrusted-identity'})==200
print('REAL_TAILNET_PRIMARY: anonymous=401 spoof_only=401 real_basic=200 mixed_basic=200 (no bodies or secrets read)')
url='https://x1-370.tailcb8954.ts.net:8443/api/v1/auth/whoami'
with urllib.request.urlopen(url,timeout=6) as response:
 data=json.load(response)
 assert response.status==200 and data.get('authenticated') is True and data.get('provider')=='tailscale'
print('MOBILE_SSO_STILL_VALID: true (identity withheld)')
PY
systemctl --user daemon-reload
[[ "$(systemctl --user is-active "$PRIMARY")" == active ]]
[[ "$(systemctl --user is-enabled "$PRIMARY")" == enabled ]]
[[ "$(systemctl --user is-enabled "$OLD")" == disabled ]]
trap - ERR
cutover_started=0
echo 'SANITIZED_PRIMARY_ACTIVE_VERIFIED: legacy disabled; mobile SSO intact; production cutover static gate passed (broader release still HOLD)'
