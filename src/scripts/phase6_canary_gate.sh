#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
AUTH_USER="${BASIC_AUTH_USER:-}"
AUTH_PASS="${BASIC_AUTH_PASS:-}"

MAX_QUEUE_DEPTH="${MAX_QUEUE_DEPTH:-25}"
MAX_FAILED_JOBS="${MAX_FAILED_JOBS:-20}"
MAX_STALE_SESSIONS="${MAX_STALE_SESSIONS:-20}"
MAX_FAILED_DISPATCHES="${MAX_FAILED_DISPATCHES:-20}"
STALE_MINUTES="${STALE_MINUTES:-30}"
MAX_REVIEW_BACKLOG="${MAX_REVIEW_BACKLOG:-25}"
REVIEW_SLA_MINUTES="${REVIEW_SLA_MINUTES:-60}"

if [[ -z "${AUTH_USER}" || -z "${AUTH_PASS}" ]]; then
  echo "Set BASIC_AUTH_USER and BASIC_AUTH_PASS"
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

curl -fsS -u "${AUTH_USER}:${AUTH_PASS}" \
  "${BASE_URL}/api/ops/status?stale_minutes=${STALE_MINUTES}&review_sla_minutes=${REVIEW_SLA_MINUTES}&review_backlog_threshold=${MAX_REVIEW_BACKLOG}" > "${tmp}"

read_json() {
  local expr="$1"
  python3 - "$tmp" "$expr" <<'PY'
import json, sys
path, expr = sys.argv[1], sys.argv[2]
obj = json.load(open(path))
for part in expr.split("."):
    obj = obj[part]
print(obj)
PY
}

neo_status="$(read_json neo4j.status)"
queue_depth="$(read_json queue.depth)"
queue_failed="$(read_json queue.failed)"
stale_sessions="$(read_json sessions.stale)"
failed_dispatches="$(read_json dispatches.failed_or_cancelled)"
review_backlog="$(read_json review.backlog)"
review_oldest_age="$(read_json review.oldest_age_minutes)"
review_sla_breached="$(read_json review.sla_breached)"

echo "[canary] neo4j.status=${neo_status}"
echo "[canary] queue.depth=${queue_depth} (max ${MAX_QUEUE_DEPTH})"
echo "[canary] queue.failed=${queue_failed} (max ${MAX_FAILED_JOBS})"
echo "[canary] sessions.stale=${stale_sessions} (max ${MAX_STALE_SESSIONS})"
echo "[canary] dispatches.failed_or_cancelled=${failed_dispatches} (max ${MAX_FAILED_DISPATCHES})"
echo "[canary] review.backlog=${review_backlog} (max ${MAX_REVIEW_BACKLOG})"
echo "[canary] review.oldest_age_minutes=${review_oldest_age} (sla ${REVIEW_SLA_MINUTES})"
echo "[canary] review.sla_breached=${review_sla_breached}"

fail=0
[[ "${neo_status}" != "ok" ]] && fail=1
(( queue_depth > MAX_QUEUE_DEPTH )) && fail=1
(( queue_failed > MAX_FAILED_JOBS )) && fail=1
(( stale_sessions > MAX_STALE_SESSIONS )) && fail=1
(( failed_dispatches > MAX_FAILED_DISPATCHES )) && fail=1
(( review_backlog > MAX_REVIEW_BACKLOG )) && fail=1
[[ "${review_sla_breached}" == "True" ]] && fail=1

if [[ "${fail}" -ne 0 ]]; then
  echo "[canary] FAIL: one or more gates exceeded."
  exit 1
fi

echo "[canary] PASS: all gates satisfied."
