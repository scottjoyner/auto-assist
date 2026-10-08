# Preregistered prospective test — Neo4j 5.26 persistent-driver trace outcome acceptance
**Frozen October 8, 2026 EDT prior to generating or measuring the Neo4j 5.26 fixture.**

## Context and hypotheses
Prior isolated Neo4j 5.23 test of 85,000 generated groups and 170,000 events returned correct totals; process-wall samples around 2.4–2.7 seconds included JVM CLI startup and could not assess the real driver's 4-second timeout or execution-plan DB hits. Previous synthetic Chromium/axe checks passed, but actual authenticated UI tests remain open. This test is **a new measurement**, not an extension of a production run.

Hypotheses:
1. With identical deterministic fixture distribution, 5.26 returns 34,000 failed; 34,000 completed; 17,000 open, with mixed failed/completed classified failed.
2. A **persistent Python Neo4j 6.2 driver connection** should eliminate repetitive cypher-shell process overhead. Read-only count and first-page queries will have distinct p50/p95 execution times; under 1 CPU / 2200 MiB they may breach four-second transaction caps. We accept failure/timeout as negative evidence, never raise budgets silently.
3. Read-only EXPLAIN/PROFILE on this **disposable synthetic graph only** will reveal plan operators and total DB hits. Correlated EXISTS may cause label scans and expensive per-group membership, especially on a growing corpus; indexes should be recorded rather than assumed to help.
4. Driver concurrency 1, 3, 5 on a CPU-capped instance will increase contention. Stop/reduce workload on timeouts, container OOM or shared-host pressure.
5. An off-host/authenticated browser remains untested. Even if synthetic tests pass, no production rollout, historical audit custody or provenance attestation is authorized.

## Exact isolation and bounded method
- Docker image already cached locally: neo4j:5.26-enterprise (actual release to record from RETURN version). Disposable name **assistx-tracebench-526-20261008**; Docker network **assistx-tracebench-isolated-20261008** marked *internal=true*, no external egress, no host bind mounts; only published port **127.0.0.1:17687→7687** for a loopback-only Python driver. Restricted to 1 CPU and 2200 MiB memory, PID cap 256, no-new-privileges. NEO4J_AUTH=none only inside this test container. No secrets mounted.
- Enforce docker inspect preflight checking name/image, internal network, private port binding, no bind mounts, resource caps, no live Neo4j container or credential use. The target is hard-coded to bolt://127.0.0.1:17687; a sentinel and zero pre-existing TraceGroup count must be verified before fixture writes.
- Generate 85,000 *invented* TraceGroup IDs synthetic-0..synthetic-84999 and 170,000 invented TraceEvent nodes in bounded transactions. Five-step modulo fixture ensures mixed failure/accept, failed-only, completed, accepted and open counts. No real event text, user content or files.
- With persistent driver, record a warm-up, 5 sequential samples per outcome/count+page, elapsed per individual query and combined list operation. Record read-only server PROFILE DB hits and operator names **only on synthetic graph**; separate generation/checkpoint costs. Measure up to 3 parallel clients and record rates/latencies. Maximum 4-second timeout on each actual Query; use bounded clients, avoid infinite retries.
- Never point at production Bolt URI, read real histories or write production Neo4j. Remove test container, anonymous volumes and isolated Docker network regardless of success. Any partial experiment must be documented as such. All committed results aggregate/synthetic only.
- The existing auth and UI were not deployed; acceptance tracker #123 remains open until full operator review.


## Pre-measurement method amendment (October 8, 2026 EDT)
The disconnected Docker-internal bridge does not forward the requested localhost published port; `bolt://127.0.0.1:17687` returned connection refused. A read-only probe confirmed the isolated container directly at its Docker-inspected private IP `172.23.0.2:7687`. **Before synthetic fixture generation**, the benchmark will derive this private IP exclusively from `docker inspect` of the exact named container, confirm the network is `Internal=true`, the container is image 5.26 Enterprise, no bind mounts/secrets, restricted CPU/memory and empty TraceGroup inventory. The driver is hard-wired to that verified private IP and cannot be parameterized toward production. No test result has been collected under this amended plan yet. The container remains disposable and must be destroyed together with its internal network after testing.


## Preregistered bounded contention follow-on (before additional measurements)
The 5.26 initial driver measurements have completed. To test the original prospectus's five-client condition without widening graph content or touching production, the next pass sends **three waves of five** real read-only list-trace calls against the *same isolated staging container*, with each call performing the existing two 4-second-timeout read queries. Record each total request wall time and timeout/error, without automatic retries. Cap to at most 15 requests and abort rather than relax the resource guard. This is a deliberately small contention screen, not a credible production p95/p99 estimate or an enterprise load test.
