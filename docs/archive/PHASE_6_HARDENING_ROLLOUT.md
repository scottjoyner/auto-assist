# Phase 6 Hardening and Rollout Runbook

This runbook covers the remaining production rollout steps that are operational
rather than code-only.

## 1. Preconditions

- `docker compose -f docker-compose.yml -f compose.prod.yml config --quiet` passes.
- Test suite passes in a venv:
  - `PYTHONPATH=src .venv/bin/python -m pytest -q`
- Required secrets are set in deployment env:
  - `BASIC_AUTH_USER`, `BASIC_AUTH_PASS`
  - `PAPERCLIP_WEBHOOK_SECRET`
  - `VOICE_WEBHOOK_SECRET`
  - `WS_AUTH_TOKEN`
  - `PAPERCLIP_API_TOKEN`

## 2. Security Controls Checklist

- Paperclip callbacks require HMAC signatures (`X-Paperclip-Signature`).
- Voice callbacks require either:
  - valid Basic/trusted auth, or
  - HMAC signature (`X-Voice-Signature`) with `VOICE_WEBHOOK_SECRET`.
- Websocket auth enabled:
  - `WS_AUTH_REQUIRED=1`
  - `WS_AUTH_TOKEN` set to a strong random value.
- Rate limiting enabled on:
  - `/api/dispatch`
  - `/api/paperclip/events`
  - `/api/ask`
  - `/api/intents`

## 2.1 Scripted Checks

Run these from repo root:

```bash
src/scripts/phase6_preflight.sh
src/scripts/phase6_callback_smoke.sh
src/scripts/phase6_canary_gate.sh
```

Helpful env overrides:

```bash
BASE_URL=http://localhost:8000
MAX_QUEUE_DEPTH=25
MAX_FAILED_JOBS=20
MAX_STALE_SESSIONS=20
MAX_FAILED_DISPATCHES=20
STALE_MINUTES=30
MAX_REVIEW_BACKLOG=25
REVIEW_SLA_MINUTES=60
```

## 3. Canary Rollout

1. Deploy one worker and one Hermes adapter device.
2. Keep `WORKER_CONCURRENCY=1` for first canary wave.
3. Run canary flows:
   - Create intent (`POST /api/intents`)
   - Create task and dispatch (`POST /api/dispatch`)
   - Complete task via adapter
   - Send signed Paperclip event
   - Send signed voice event (`POST /api/voice/events`)
4. Monitor:
   - `GET /api/ops/status`
   - `GET /metrics`
   - `GET /api/dispatches`
   - `GET /api/sessions`
5. Success gates (30-60 min):
   - no sustained queue growth
   - no repeated dispatch failures
   - no auth/signature failures for valid callbacks
   - stale sessions stable or decreasing
   - review backlog below threshold and no review SLA breach

## 4. Scale-Up Plan

1. Increase `WORKER_CONCURRENCY` gradually (1 -> 2 -> 4).
2. Add remote Hermes devices in batches.
3. Validate after each batch:
   - queue depth
   - failure rate
   - task completion latency

## 5. Rollback Plan

If rollout regresses:

1. Pause new dispatch creation at the operator layer.
2. Keep read paths available (`/api/*` GET endpoints and UI).
3. Keep pollers on or off depending on failure domain:
   - event storm/faulty callbacks: disable callback senders first
   - queue overload: scale workers down and drain queue
4. Revert to previous deployment image/tag.
5. Validate:
   - `GET /health`
   - `GET /api/ops/status`
   - task claim/complete happy path

## 6. Post-Rollout Verification

- Validate signed callback replay safety by re-sending same `event_id`; state
  should remain idempotent.
- Verify maintenance job is active:
  - retention deletes old terminal tasks and old memory records
  - answers index reindex runs successfully.
- Record outcomes and timestamps in deployment notes.
