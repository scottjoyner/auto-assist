# Preregistered performance experiment — AssistX global trace outcome filters
Date: 2026-10-08 EDT. Frozen before starting a disposable graph benchmark.

## Question / predictions
The global outcome filtering in draft auto-assist PR #122 applies correlated Cypher EXISTS predicates in both a global count and a paged query. At realistic volume the cost may exceed the nominal 4-second per-query guardrail.

H1: Against a disposable isolated Neo4j 5.26 container with no network exposure, 85,000 generated TraceGroup nodes and two synthetic events per group can be created within 1 CPU and 2 GB RAM. No real trace identifiers, payloads, credentials or user data.

H2: Failed/completed/open outcomes match deterministic event fixtures, including mixed failure+completion where failure dominates. Count and page query latency may differ; any timeout is a negative release observation, not a reason to silently raise budgets.

H3: Read-only EXPLAIN exposes whether count/page predicates require expensive graph expansion. Improvements must preserve matching count/page inclusion rules without adding read authority over payloads.

## Method/constraints
- Disposable --rm Docker container, --network none, CPU <=1, memory <=2GB, pids-limited, no bind mounts/ports. NEO4J_AUTH=none is only for a disconnected disposable container.
- Write bounded synthetic Cypher fixture and record exact expected outcome counts, read-only query plan, and timed count/page requests. Explicitly distinguish CLI startup from server query costs.
- Do not connect to existing neo4j, assistx-api, assistx-worker, NAS or any other production service; no real traces.
- Stop on resource pressure/timeouts, preserve draft-only release state and always remove disposable container.

## Release gate
Synthetic staging does not establish production concurrency or real authenticated performance. Historical full-fidelity tool-call custody remains a separate requirement.
