# Neo4j Context Alignment Event

## Purpose

This repo alignment locks in Neo4j as the canonical context fabric for agents, tasks, node identity, model inventory, execution lanes, and artifact metadata.

The goal is to make context look the same whether a worker is running locally, using a free API credit lane, or executing through the current Paperclip cutover path.

## Shared contract

- Neo4j is the source of truth for task state, agent context, node capabilities, model endpoint inventory, policy decisions, and artifact references.
- AssistX owns the graph authority and the event ingestion path.
- auto-router owns execution selection, provider dispatch, streaming responses, and lane-aware quota handling.
- Local-only, free-API, and Paperclip-backed execution must be explicit graph facts, not implicit code paths.
- Free API credits are legitimate quotas and model lanes, not account rotation or limit evasion.

## Graph facts to standardize

- `SwarmNode`: who is running locally, what they can do, and whether they can execute offline.
- `ModelEndpoint`: which providers or local endpoints are available, plus lane and capability metadata.
- `ExecutionLane`: `local`, `free_api`, `paperclip`, `blocked`.
- `QuotaBucket`: request, token, or monthly credit buckets that can be consumed legitimately.
- `AgentRun`: task execution state and output provenance.
- `Artifact`: the files, diffs, logs, or outputs produced by a run.
- `PolicyDecision`: why a request was routed local-only, free-credit, or deferred.

## Alignment event outcomes

1. AssistX publishes the canonical graph projection for node, model, policy, and artifact state.
2. auto-router consumes that projection and makes provider and lane decisions from it.
3. Both repos expose the same lane terminology in their dashboards and API metadata.
4. AssistX exposes the projection at `/api/context/projection` for live consumers.
5. Paperclip remains the current non-realtime release path until the production-worker canary completes.
6. AssistX runtime now reports dependency health explicitly and only falls back to in-memory shims when the dependency mode is set for compat/test environments.

## Next steps

- Add a graph-backed registry projection for nodes, models, and quotas.
- Make router policy read lane and locality metadata from the shared context projection.
- Record execution provenance back into Neo4j so agents can see what ran locally versus on free API lanes.
- Keep deferred swarm/direct-worker routing separate from the Paperclip cutover release path.
