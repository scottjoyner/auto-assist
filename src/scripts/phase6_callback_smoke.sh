#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
AUTH_USER="${BASIC_AUTH_USER:-}"
AUTH_PASS="${BASIC_AUTH_PASS:-}"
PAPERCLIP_WEBHOOK_SECRET="${PAPERCLIP_WEBHOOK_SECRET:-}"
VOICE_WEBHOOK_SECRET="${VOICE_WEBHOOK_SECRET:-}"
PYTHONPATH="${PYTHONPATH:-src}"
export PYTHONPATH
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RID="$(date +%s)"
export TS RID

if [[ -z "${AUTH_USER}" || -z "${AUTH_PASS}" ]]; then
  echo "Set BASIC_AUTH_USER and BASIC_AUTH_PASS"
  exit 1
fi
if [[ -z "${PAPERCLIP_WEBHOOK_SECRET}" || -z "${VOICE_WEBHOOK_SECRET}" ]]; then
  echo "Set PAPERCLIP_WEBHOOK_SECRET and VOICE_WEBHOOK_SECRET"
  exit 1
fi

pc_payload="$("${PYTHON_BIN}" - <<'PY'
import json, os
payload = {
    "event_type": "run_completed",
    "paperclip_issue_id": f"smoke-issue-{os.environ['RID']}",
    "paperclip_agent_id": "smoke-agent",
    "paperclip_run_id": f"smoke-run-{os.environ['RID']}",
    "event_id": f"smoke-paperclip-{os.environ['RID']}",
    "payload": {"status": "DONE", "source": "phase6_smoke"},
}
print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
PY
)"
export pc_payload

pc_sig_raw="$("${PYTHON_BIN}" - <<'PY'
import hashlib, hmac, os
payload = os.environ["pc_payload"].encode("utf-8")
secret = os.environ["PAPERCLIP_WEBHOOK_SECRET"].encode("utf-8")
print(hmac.new(secret, payload, hashlib.sha256).hexdigest())
PY
)"
pc_sig="sha256=${pc_sig_raw}"

echo "[smoke] POST /api/paperclip/events"
curl -fsS \
  -H "Content-Type: application/json" \
  -H "X-Paperclip-Signature: ${pc_sig}" \
  -d "${pc_payload}" \
  "${BASE_URL}/api/paperclip/events" >/dev/null
echo "  - OK"

voice_payload="$("${PYTHON_BIN}" - <<'PY'
from assistx.api import VoiceEventIn
import os
payload = {
    "event_id": f"smoke-voice-{os.environ['RID']}",
    "event_type": "tts_chunk",
    "text": "remember to check canary metrics",
    "source": "voice",
    "client_ts": os.environ["TS"],
    "metadata": {"source": "phase6_smoke"},
}
print(VoiceEventIn(**payload).model_dump_json(exclude_none=True))
PY
)"
export voice_payload

voice_sig_raw="$("${PYTHON_BIN}" - <<'PY'
import hashlib, hmac, os
payload = os.environ["voice_payload"].encode("utf-8")
secret = os.environ["VOICE_WEBHOOK_SECRET"].encode("utf-8")
print(hmac.new(secret, payload, hashlib.sha256).hexdigest())
PY
)"
voice_sig="sha256=${voice_sig_raw}"

echo "[smoke] POST /api/voice/events (signed callback mode)"
curl -fsS \
  -H "Content-Type: application/json" \
  -H "X-Voice-Signature: ${voice_sig}" \
  -d "${voice_payload}" \
  "${BASE_URL}/api/voice/events" >/dev/null
echo "  - OK"

echo "[smoke] GET /api/ops/status"
curl -fsS -u "${AUTH_USER}:${AUTH_PASS}" "${BASE_URL}/api/ops/status" >/dev/null
echo "  - OK"

echo "[smoke] Callback smoke checks complete."
