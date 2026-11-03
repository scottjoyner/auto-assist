# Legacy fleet worker: exact target and terminal receipt binding (source-only)

## Physical context, November 3, 2026

Fleet Commander reached nine fleet Linux hosts, finding 18 responsive local
OpenAI-style model listeners but only four **native-confirmed chat instances**
(three Ornith instances, one LFM2.5) and one native-confirmed embedding instance.
The five running legacy fleet-node-agent services still receive HTTP 401 on
AssistX task listing using their effective Basic credentials. The canonical
signed work-supply projection has expired: background Tasks are disabled and
approved slots are zero. Auto-Router remains degraded with zero enabled free
hosted API provider routes. An existing READY Task has no explicit assigned
node or machine Task authorization. None of these blocks is cleared here.

In a three-second read-only /proc CPU-jiffy sample, inference-named processes
across the nine hosts showed little CPU activity. This does **not** measure
GPU/NPU decode, generation tokens, task quality or the whole day, and must not
be used to assert idleness conclusively.

## Concrete source contract defect

Base draft [PR #482](https://github.com/scottjoyner/auto-assist/pull/482)
already fixes legacy agent GET state/status, per-node filters, no-model LLM
advertisement, 401/403 polling backoff, nested claim IDs, nonproductive model
results and unacknowledged completion. Its current worker still allowed
`READY` records with `target_agent_id=null`, and silently normalized missing
required capabilities to an empty list; the existing generic backend claim
also accepts NULL targets and empty capability wildcard. When agent
credentials are repaired, those are not safe machine-work permissions.

The worker also considered `/api/tasks/{id}/complete` accepted after HTTP200
with matching `task.id` and terminal `task.status`. Legacy Neo4j completion
has an idempotency/replay path capable of returning another attempt's terminal
Task snapshot; an ID/status match **does not verify claim ownership**.

## New local source fence

The legacy `_poll_once()` now calls claim only for a READY Task with **exact**
`target_agent_id=node_id`, `required_capabilities=['llm']`, advertised `llm`,
and no non-LLM task type. No shared-pool untargeted, absent/empty capability,
broad tool-capability, script/recovery or arbitrary shell job is treated as
machine-authorized. This is intentionally more restrictive than the backend's
still-broad generic Task claim API.

After a 200 claim, `_claim_and_run()` checks the **authoritative claimed Task**
again: exact Task ID, node owner, session, claim ID, status CLAIMED, target,
`['llm']`, permitted task type, advertised `llm`, and **positive integer**
`execution_attempt` (booleans excluded). Any mismatch rejects before model
inference or heartbeat; it cannot silently enlarge machine tool authority.

After inference and completion POST, the *returned* terminal Task must match
**all** of Task ID, terminal status, `claimed_by`, `claim_id`,
`agent_session_id`, and exact positive integer execution attempt from the
server claim. An HTTP200 with another Task's owner, stale/replayed claim,
missing or wrong session, or different attempt returns **False** to the
worker; it never counts as acknowledged work. One completion request is made;
the worker does not blindly replay inference. This does not prove an external
result custodian accepted the output.

## Tests and release gates

**78 local source/security tests passed** on this stacked branch: 66 worker
poll/claim/generation and 12 node-identity/executor adjacency tests, including
30 new parametrized negatives. Existing source static Ruff, byte-compilation
and whitespace checks passed. The tests are synthetic and make **no live Task
claim, model execution, credential change, graph mutation or agent restart**.

The generic backend claim/heartbeat/completion transaction still retains
P0 #428 owner/session/lease/generation/idempotency issues; the source changes
in [PRs #504, #508, #509](https://github.com/scottjoyner/auto-assist/pull/509)
are draft and not deployed. There are no current signed machine admission
slots. Therefore this branch MUST stay draft and be reviewed alongside the
five-node scoped credential migration [issue #501](https://github.com/scottjoyner/auto-assist/issues/501).

**Production NO-GO:** no new Task work authorized; do not cut over legacy
Basic credentials or interpret a matched HTTP terminal acknowledgement as an
independent cryptographic Task-result receipt. The backend Task transaction
and independently verified work supply must be fixed before enabling any
automatic productive claims.
