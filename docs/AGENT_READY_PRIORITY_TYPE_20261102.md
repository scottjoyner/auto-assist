# AssistX READY feed: non-text Task.priority safety fix

## Read-only production evidence (2026-11-02, x1-370)

An authenticated **read-only** `GET /api/agent/tasks?status=READY&limit=1` returned HTTP 500; the same result occurred for limits 3, 20, 50, and 100. Authenticated `/api/tasks` and `/api/workflows/queue` returned 200, and a capability- and agent-targeted Hermes READY feed returned 200. The error was thus dependent on the selected records, not a universal auth or DB outage.

Deployed traceback: `Neo4jClient.list_agent_tasks` sorted an in-memory list with `(t.get("priority") or "UNSET").upper()`, raising `AttributeError: 'int' object has no attribute 'upper'`. At least one READY task has an integer priority in Neo4j.

## Change and security properties

The in-memory READY-task selection is **fail-closed** for explicitly non-text priorities: those malformed tasks remain visible to authenticated operators through `/api/tasks`, but are excluded from `/api/agent/tasks`. Missing/null priority (legacy UNSET) remains possible and ranks last; known text priorities are trimmed, case-normalized and ordered by existing rank. Numeric values such as `100` are never promoted or made newly claimable, and no Task is mutated. Existing agent-id, capability, paperclip dispatch, workflow control, authentication, claim, and Neo4j write semantics remain unchanged. This does not justify requeuing any FAILED task or overriding a security hold.

Focused tests use in-memory fake Neo4j responses to cover mixed numeric/float/text/null priorities, fail-closed malformed-task exclusion, target/capability filters and the authenticated FastAPI READY route via a temporary test-only auth override. No production data is written. The live API still requires deploy/review; no runtime changes made.

Further authenticated read-only production audit found exactly **one** READY Task at the time; it has integer priority and missing required capabilities/payload metadata. Therefore merely coercing its sort key would have surfaced an ambiguous legacy Task for execution. The final fix excludes it from the claimable feed rather than upgrading its eligibility. A separate operator action is required to repair or replace Task authority and supply safe work. No live task was modified.
