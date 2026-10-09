# Trace-index physical-slot quarantine — synthetic runbook

**Scope:** October 8, 2026 research on x1-370. This is a standalone, unused module **not wired into AssistX**. No real provider, Neo4j, Redis or trace-source data are required.

## Synthetic unit acceptance

```bash
cd /home/scott/git/wt-assistx-trace-quarantine-20261008
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_index_quarantine.py \
  tests/test_trace_index_cancellation.py \
  tests/test_trace_index_inflight.py \
  tests/test_trace_index_admission.py \
  tests/test_trace_outcome_filter.py
```

**Observed:** 118 passes with one existing Starlette/TestClient deprecation warning. The passing suite contains a deliberate negative control demonstrating over-admission when Redis state is lost. Green tests are **not** physical-fence certification.

## Optional isolated Redis Lua replay (never production)

**Only execute on a machine where starting a tiny disposable container is authorized.** The fixture uses its exact isolated name, no network/ports/binds, 1 CPU, 128 MiB RAM, and no persistence. Check no container exists under this name before starting.

```bash
docker run --rm -d --name assistx-quarantine-test-20261008 \
  --network none --cpus 1 --memory 128m --memory-swap 128m \
  --pids-limit 96 --security-opt no-new-privileges \
  redis:7-alpine redis-server --save '' --appendonly no

PYTHONPATH=src python3 tests/probe_trace_index_quarantine_redis.py

# Stop/remove **only** this explicitly named disposable fixture.
docker stop --time 3 assistx-quarantine-test-20261008
docker rm -f -v assistx-quarantine-test-20261008 2>/dev/null || true
```

The probe inspects the exact Docker name, image, disconnected network, caps, bind mounts, and port publishing before invoking Redis. It never connects over a host port and never reads production Redis configuration. The synthetic negative control explicitly deletes the **research-only** Redis key after strict target verification.

## Interpretation and deployment stop
This experiment prevents TTL expiry from automatically replacing active slots *while the Redis key survives*. The price is indefinite occupancy after worker crash. Redis key loss/failover remains a counterexample; signed remote cancellation and durable independent physical query ownership are not yet available. The Python method's `remote_query_termination_verified=True` flag is **only a caller assertion**, not proof of actual remote cancellation. No production route imports this class, and no API/Neo4j/Redis/collector/NAS activation is authorized.

Next acceptance is to design a durable physical query ownership/termination witness robust across Redis failover and API worker death, implement authenticated 1/3/5/10-worker contention with real graph and trusted proxy topology in a staging system, and define fail-closed rollback. Keep [#148](https://github.com/scottjoyner/auto-assist/issues/148) and [#123](https://github.com/scottjoyner/auto-assist/issues/123) OPEN.
