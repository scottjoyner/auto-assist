# AssistX Neo4j Consumer Census — B02 Release-Blocking Evidence
**Assessment:** October 11, 2026  
**Scope:** static Python AST inspection of `src/assistx`, no live graph reads, credentials or service changes  
**Disposition:** **B02 still P0 / not cleared**. Staging read-only broker canary remains conditional; live network gate B01 still fails.

## Verified source inventory (current stacked branch)

The executable `tests/trace_graph_consumer_census.py` analyzed the current checked-in `src/assistx` tree. It recorded **72 declared Neo4jClient methods**, **149 potential graph-client method call sites** (excluding ambiguous `close` and `_session`), **194 direct driver/session calls outside `neo4j_client.py`**, and **57 files** containing those classes of references. Direct session calls occur in **47 files**.

**These numbers are call-site indicators, NOT 149 unique physical queries or 194 proven production graph bypasses.** AST inspection can miss dynamic/aliased imports and can include unreachable or shadowed code. Every discovered read/write/session must be manually categorized and associated with a production principal, route, current Neo4j privileges and rollback before live migration.

Top source files by *combined static indications*:

| File | AST call-site indications |
|---|---:|
| `api.py` | 104 |
| `swarm_core.py` | 27 |
| `intent_orchestrator.py` | 16 |
| `routers/review.py` | 11 |
| `agents/orchestrator.py` | 10 |
| `voice_routes.py` | 9 |
| `routers/feeds.py` | 8 |
| `kg_harvester.py` | 8 |
| `jobs.py` | 7 |
| `self_healing.py` | 7 |

Top client method calls (each *potential* caller; source-path evidence, not execution):

| Method | Sites |
|---|---:|
| `update_task_status` | 11 |
| `create_task_with_context` | 10 |
| `get_task` | 9 |
| `log_tool_call` | 8 |
| `upsert_ticket` | 7 |
| `complete_run` | 5 |
| `upsert_intent` | 5 |
| `ensure_schema` | 5 |
| `complete_deliverable` | 4 |
| `create_workflow_incident` | 4 |

## Nine specifically reviewed `get_task` sites

| File:line | Consumer | Release-critical observations | Present covered projection |
|---|---|---|---|
| `api.py:4301` | `api_claim_task` | Recovery-node identity verified conditional on `_is_recovery_task`; drain permission based on `_is_claim_allowed_for_workflow_control`; actual `claim_task` writes and lease/capabilities remain separate | **Partial** — #296's six-field claim guard covers two source helpers only |
| `api.py:4337` | `api_heartbeat_task` | Recovery identity check precedes `neo.heartbeat_task`; returned complete Task drives metrics and HTTP result | **Partial** — guard-only, heartbeat and response parity outstanding |
| `api.py:4366` | `api_complete_task` | Recovery identity check AND `evaluate_completion(task_before,…)`; improvement-cycle/proposal/benchmark logic consumes Task record | **NOT accepted** — additional evaluation/provenance fields not inventoried |
| `api.py:4402` | `api_complete_task` (refresh) | Refreshed complete Task can replace result when dead-letter incident recorded | **NOT accepted** |
| `api.py:5354` | `api_workflow_replan` | Task existence followed by separate direct `_session().run(SET...)` write, plus incident write | **NOT accepted** — must convert read and write as single reviewed command |
| `api.py:5387` | `api_workflow_budget_update` | Task existence followed by `upsert_workflow_budget` write | **NOT accepted** |
| `routers/review.py:162` | `api_approve_review_task` | Requires `status=REVIEW`, `payload_json`, fallback `title`, `source_text`, `source_intent`, `policy_action`, then creates new task/dispatch | **NOT accepted** — current six-field claim guard does not return `title`, nor authorize downstream writes |
| `routers/review.py:234` | `api_reject_review_task` | Requires review status; direct Cypher `SET` updates cancellation and review metadata | **NOT accepted** |
| `routers/review.py:261` | `api_clarify_review_task` | Requires review status; direct Cypher `SET` stores decision/note/reviewer/timestamps | **NOT accepted** |

**New blocker:** `api_approve_review_task` uses `review_task.get("title")` as a fallback when payload source text is absent. The #296 claim-policy projection intentionally omits `title`. Returning only that projection could silently change the *content of an approved task* or allow partial decision context. **Do not reuse claim-guard plan in the review routes.** A separately reviewed review-only plan and a direct Neo4j write command must be accepted together, preserving transaction and authorization semantics.

The existence/read check for workflow replan is also not enough: it is immediately followed by direct `neo._session` writes and a workflow incident mutation. Moving only the read behind the broker leaves B01 bypass intact and can introduce a race between read and mutation.

## Mandatory B02 acceptance before staging migration

1. **Enumerate and classify all identified callers**, including `_session()` in 47 modules: source file/function/line, actual runtime feature, `READ`/`WRITE`/`SCHEMA`/`UNKNOWN`, graph labels/properties, sensitive data, service principal, fail-open/fail-closed behavior and owner. Label unreviewed calls `UNKNOWN` rather than assuming read-only.
2. **Per-consumer reviewed plan & field registry:** separate ordinary Task read, claim/recovery, heartbeat, completion/improvement, review approval, review rejection/clarification, workflow control, and administrative/schema operations. Avoid a generic proxy with raw Cypher.
3. **Differential authorization and behavior fixtures** against current source: recovery identities, drain-critical/interactive, claim lease/idempotency, review fallback title, approval creates task, rejection/clarification updates exact properties, completion improvement evaluation, dead letters and incident workflows. Include negative fields/partial projections.
4. **One joined physical transaction chain (B03)**: authenticated stable operation → Raft CAS → exclusive broker's Neo4j transaction → independent closure witness → exact Raft release. Deny direct worker Bolt/HTTP and forged/stale grants. Existing physical results are *separate subprobes*.
5. **Staging-only controlled migration** of one chosen low-risk read consumer before any general graph network cutover. Prove outputs and p95/p99, rollback, user/tenant identity; do not disable live alternate graph paths until all legitimate production callers have migrated.

## Live B01 is separately red

The fresh read-only fleet Docker/TCP gate on x1-370 on October 11 still returned `NETWORK_BYPASS_PRESENT_NO_GO`: running `assistx-worker` and `assistx-api` share an accessible Neo4j Docker network and have a direct Bolt TCP path. Wildcard-published host Bolt exists. **No Cypher or credential authorization was tested.** This gate is not fixed by the current new source inventory or the disposable #296 broker.

## Evidence and repeatability

- Source-only inventory: `PYTHONPATH=tests python3 tests/trace_graph_consumer_census.py --root src/assistx`
- Offline scanner tests: `PYTHONPATH=tests pytest --noconftest -q tests/test_trace_graph_consumer_census.py`
- Follow-on research release report: [TRACE_GRAPH_ADMISSION_RELEASE_READINESS_20261011.md](./TRACE_GRAPH_ADMISSION_RELEASE_READINESS_20261011.md)
- Parent policy-preserving broker: [draft PR #296](https://github.com/scottjoyner/auto-assist/pull/296)
- P0 tracking: [issue #148](https://github.com/scottjoyner/auto-assist/issues/148)

**Decision:** B02 P0 open; production broker/trace-index/failover remain NO-GO. Safe research documentation and offline CI may proceed.
