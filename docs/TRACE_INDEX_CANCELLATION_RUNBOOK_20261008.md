# Read-only cancellation probe — disposable Neo4j 5.26 staging runbook

**Status:** experimental only, not executable authorization for production. The exact staging target guard in the probe rejects anything other than the expected disposable, disconnected container; it has **no CLI option accepting a production Bolt URI**.

## Reproduce offline tests (no Docker/Neo4j or production calls)
```bash
cd /home/scott/git/wt-assistx-trace-cancellation-20261008
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_index_cancellation.py \
  tests/test_trace_index_inflight.py \
  tests/test_trace_index_admission.py \
  tests/test_trace_outcome_filter.py
```
**Observed:** 78 passing Python tests. The new 13-case suite intentionally reproduces TTL-expiry allowing a second physical query while the first is still logically active. This is an unsafe guarantee counterexample, not a simulated real production incident.

## Optional operator-owned isolated Neo4j probe
Only if CPU/RAM/storage budget and host condition are acceptable. Do **not** use existing Neo4j, Assistant API, NAS, Redis or production credentials. The probe uses synthetic *read-only arithmetic*, no private trace contents or persistent seed.

```bash
set -euo pipefail
NAME=assistx-trace-cancel-stage-20261008
NET=assistx-trace-cancel-isolated-20261008

cleanup() {
  docker rm -f -v "$NAME" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create --internal --driver bridge "$NET"
docker run -d --name "$NAME" --hostname trace-cancel \
  --add-host trace-cancel:127.0.0.1 --network "$NET" \
  --cpus 1 --memory 2200m --memory-swap 2200m --pids-limit 256 \
  --security-opt no-new-privileges \
  -e NEO4J_AUTH=none -e NEO4J_ACCEPT_LICENSE_AGREEMENT=eval \
  -e NEO4J_server_memory_heap_initial__size=512m \
  -e NEO4J_server_memory_heap_max__size=512m \
  -e NEO4J_server_memory_pagecache_size=256m \
  neo4j:5.26-enterprise

# Allow service health in a bounded loop; abort on failure.
ready=0
for i in $(seq 1 30); do
  if docker exec "$NAME" cypher-shell \
      -a bolt://localhost:7687 'RETURN 1 AS healthy' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
test "$ready" -eq 1

PYTHONPATH=src timeout 60 python3 -u tests/probe_trace_index_cancellation_526.py
```

The first run's short arithmetic query was inconclusive; the amended second run generated a genuine 1ms transaction timeout error and completed at the other two thresholds. Its transaction inspection returned zero remaining matching benchmark statements. Reports live under `docs/TRACE_INDEX_CANCELLATION_LIGHT_PROBE_20261008.json` and `docs/TRACE_INDEX_CANCELLATION_RESULT_20261008.json`, synthetic-only. Neither establishes hard physical query fencing after the lease TTL expires.

## Next acceptance gate
Keep [#148](https://github.com/scottjoyner/auto-assist/issues/148) and overall UI [#123](https://github.com/scottjoyner/auto-assist/issues/123) OPEN. Require query identity/cancel acknowledgment or conservatively retained capacity, transport stall/restart/Redis failover and reverse-proxy identity tests, and authenticated real multiworker 1/3/5/10 contention before activating any concurrency feature. This research branch is stacked on draft #154 and is not deployment authority.
