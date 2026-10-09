# Prospectus: never-expire physical-slot quarantine countermeasure
**Date:** 2026-10-08 EDT. **Repository:** private scottjoyner/auto-assist research branch, stacked on draft cancellation PR #160. **Status:** preregistered before new implementation/tests.

## Known falsification and question
Draft #160 demonstrated that Redis TTL-based in-flight admission can over-admit physically active Neo4j queries when a 30-second lease expires. A Redis soft lease controls only Redis membership, not remote database cancellation. We will assess a *deny-first experimental alternative* that retains admission occupancy until the exact in-flight query completion is acknowledged, **with no automatic TTL-based admission replacement**.

## Predicted outcomes
1. A never-expiring Redis hash occupancy token limits admitted physical queries to a configured cap in synthetic single-Redis tests even if simulated clock/time exceeds a former lease TTL. No automatic reaping, heartbeat expiry, or crash-based auto-release.
2. Only a matching exact token can release its own occupancy, and a stale/wrong token cannot remove another worker. Invalid/malformed Redis replies and failures deny admission.
3. A worker crash leaves capacity **quarantined**. This is deliberately a denial-of-service/liveness tradeoff, not a resolved crash cleanup process; manual authenticated reconciliation after independent graph cancellation is necessary.
4. Multiple same-client concurrent requests are counted separately; Redis atomic scripts preserve cap when competing for slots.
5. Redis data loss, split-brain, failover or an external DEL/FLUSH can reset occupancy despite a still-running Neo4j query. The prototype must retain an explicit negative control rather than claiming cross-failure physical fencing.
6. No production sources/routes/Redis keys are mutated or enabled. The module is research-only and not wired to FastAPI. An isolated optional Redis container, if used, will be disconnected with no ports or bind mounts, and removed afterward.

## Acceptance and measurements
Write strict static/simulated unit cases for acquire/release, cap, long elapsed times, crash quarantine, malformed Redis, wrong-token removal and Redis reset over-admission. Where practical validate Lua atomicity in a disposable Redis container. Record both pass and negative results. Synthetic tests only; no live Neo4j reads, real user trace content, provider calls, NAS changes or service restarts. The release NO-GO #148 and overall UI #123 remain open pending physical query cancellation acknowledgement, Redis durability/failover, source identity, authenticated multiworker tests, and owner approval.
