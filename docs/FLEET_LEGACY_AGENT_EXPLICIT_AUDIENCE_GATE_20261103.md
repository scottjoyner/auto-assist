# Legacy fleet worker: explicitly targeted Tasks only

**DRAFT / NO DEPLOYMENT / Task production HOLD.**

This patch hardens the still-legacy client-side Task polling path on
Auto-Assist PR #482. It changes neither general human/operator queue
semantics nor the Neo4j backend. It is NOT a substitute for server-side
machine-principal authorization or a release approval.

## Verified gap

- In draft PR #482, the fleet worker issued a better-scoped
  GET with an agent_id query, but _poll_once() still accepted Tasks
  with target_agent_id missing or None.
- The actual Neo4j list_agent_tasks(agent_id=...) implementation explicitly
  includes untargeted READY Tasks, and claim_task() permits
  t.target_agent_id IS NULL OR t.target_agent_id=$agent_id.
- Therefore the agent ID query alone is an *advisory audience filter*, not
  machine authorization. Later fixing physical node HTTP 401 could expose
  untargeted work to this client unless a further guard is present.

## Narrow defensive fix

The legacy worker's poll-to-claim boundary now requires an exact
target_agent_id equal to the nonempty locally recognized node_id.
Missing/null/empty/cross-node/malformed values are rejected
without invoking claim. It additionally rejects paperclip_dispatched
items even if an upstream listing accidentally emits them. An exact
synthetic target is still permitted to reach the existing mocked claim
in a research unit test; this does NOT mean the real server authorizes it.

This preserves the existing 401/403 backoff, lease rejection, recovery
exclusion and model quality checks. No API schema, Neo4j Cypher,
credential or runtime changes.

## Tests and limits

- Legacy worker poll regression suite covers missing, null, empty, wrong,
  malformed and case/whitespace-variant targets; Paperclip provenance;
  skip-invalid/consider-exact-target; empty host identity; existing
  401/403 backoff and rejected-claim behavior.
- Exactly 47 local tests passed after positive fixtures were updated to
  model explicitly targeted Tasks.
- Hosted tests are synthetic (mocked HTTP, no network/graph/model).
  The running agent processes are UNCHANGED.
- General CI and focused exact-head workflow must be checked independently.

## Unresolved production boundaries

1. The deployed Task GET and claim endpoints still use general operator
   Basic auth; five real machine credentials returned HTTP 401. Never copy
   or delegate operator Basic credentials to any node to bypass that.
2. The graph server still permits untargeted Tasks through older paths.
   It must enforce machine-principal identity, audience, approved generation,
   explicit machine authorization and signed capacity transactionally.
   Client-side filters, including this one, are not security authority.
3. Every claim/heartbeat/completion path must bind exact machine principal,
   Task, session/claim ID, generation, attempt, signed lease and side effects.
   The existing four graph writer identities remain broadly privileged.
4. The canonical projection is expired, WorkSupplyState has background=false
   and total/free slots 0/0, independent SGNC custody is unresolved.

**Decision: production NO-GO.** No Task claim, graph mutation, credential
change, machine enrollment, router route or running worker was changed.
