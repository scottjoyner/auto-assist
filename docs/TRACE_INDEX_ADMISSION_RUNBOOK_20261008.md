# Trace-index admission runbook — synthetic only

**Status:** candidate feature, not deployed. Base UI draft #121 → global-filter draft #122 → trace-index admission child draft. No production service restarts or Redis/Neo4j/NAS modification authorized by these tests.

## Existing research evidence
The global outcome filter requires roughly 1–1.8 million DB hits per read query on the prior 85k-group synthetic Neo4j 5.26.30 fixture. To bound admission, the proposed TraceIndexLimiter uses atomic Redis TIME/Lua (12 per socket peer and 60 global requests per rolling 60 seconds). It protects only authenticated GET /api/traces, and does **not** throttle GET /api/traces/{correlation_id}. An unauthenticated or malformed request must not consume Redis slots.

## Reproduce without production connections

Use an isolated worktree:
```bash
cd /home/scott/git/wt-assistx-trace-admission-20261008

PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_index_admission.py \
  tests/test_trace_outcome_filter.py \
  tests/test_trace_526_guard.py \
  tests/test_trace_bench_guard.py

node --test tests/test_trace_investigation_ui.cjs

NODE_PATH=/home/scott/git/OmniRoute/node_modules \
CHROMIUM_PATH=/home/scott/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome \
node --test tests/test_trace_browser_acceptance.cjs
```

At checkpoint: **62 Python, 12 VM UI, 7 Chromium synthetic browser tests passed**. The isolated route FastAPI test mocks a rejecting auth dependency; untrusted/unauthorized never consumes quota. The Chromium test intercepts all URLs and does not use real history content.

## Optional real Redis Lua test

**This test creates a disposable Redis instance. It must not be aimed at live Redis or any other container.** Use only cached redis:7-alpine and zero network:

```bash
docker run -d --rm --name assistx-traceguard-20261008 \
  --network none --cpus 0.5 --memory 128m --memory-swap 128m \
  --pids-limit 64 --security-opt no-new-privileges \
  redis:7-alpine redis-server --save '' --appendonly no

python3 tests/bench_trace_index_redis_lua.py

docker stop --time 3 assistx-traceguard-20261008
```

The script rejects the wrong name, running state, image, network, port, bind mounts, CPU or memory before touching the ephemeral Redis test instance. It extracts the exact Lua script from local source syntax, uses invented key names only, and asserts local/global slots are atomically admitted and denied. At the October 8 checkpoint, this passed, and the disposable container was stopped and removed.

## Release holds

Do not apply to production until an operator reviews peer identity behind reverse proxies, rate fairness, Redis outage denial and a **separate in-flight query concurrency guard**. A 60-per-minute window can still admit a burst of 60 expensive queries; it is not an end-to-end 4-second SLA or a global concurrency fence. Verify a loaded authenticated browser with 429 Retry-After and correct 401 behavior before deployment. Preserve parent and related provenance PRs as drafts. No full-historical audit custody claim.
