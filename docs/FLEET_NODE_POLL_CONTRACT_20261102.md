# Fleet node worker polling contract — November 2, 2026

## Direct production diagnosis

On x1-370, the deployed AssistX API logs contain repeated unauthenticated/unauthorized GET requests to `/api/agent/tasks?state=READY` (HTTP 401), while a separate authenticated Hermes poll succeeds. The FastAPI endpoint actually accepts `status`, repeated `capabilities`, and `agent_id`. The old fleet node worker passed the unrecognized `state` argument and omitted the latter two, making targeted task discovery impossible and treating auth failures like an empty queue.

## Source-only remediation

1. Send the exact backend query with per-node ID and repeated capability fields; keep existing Basic Auth and atomic server-side claim.
2. Reject other-agent targets, malformed capability collections, and recovery-capability tasks. This legacy agent has no token-fenced recovery claim, heartbeat, or completion implementation. Exclude recovery from advertised polling capabilities rather than bypassing identity proof.
3. Do not advertise LLM capability when the queried local `/v1/models` response is HTTP 200 with an empty model list. This is catalog visibility, not approved runtime admission.
4. Bound consecutive HTTP 401/403 polls with a 300-second maximum delay. Never infer or log credentials. A non-auth response resets the streak.

Local tests ran against the patched source over an intact independent package copy: **18 passed, 1 dependency warning**, including original recovery and KV-cache worker tests. A corrupt Git worktree necessitated publication through the GitHub connector; the original production service was not modified.

**HOLD:** does not sign AssistX runtime projection, enable any local model, requeue historical FAILED tasks, provide node recovery tokens, or make delivered outbox events into finished work. Hosted checks and reviewed deployment are required. Preserve security and release gates.


## Claim-fenced execution follow-up (same draft)

The canonical Neo4j claim response returned on a fresh claim is
`{"claimed": true, "task": {"claim_id": "<opaque>", ...}}`, whereas the
legacy fleet worker looked only for `claim.claim_id`, resulting in null
heartbeat/completion claim IDs. The database currently permits
`($claim_id IS NULL OR t.claim_id=$claim_id)` on claimed tasks; this is a
separate P0 server gate tracked by [issue #428](https://github.com/scottjoyner/auto-assist/issues/428).

The worker now requires a nonempty nested claim ID, consistent optional
top-level ID, matching task ID, claiming agent, agent session and CLAIMED
state before inference or side effects. Crucially, it executes the
**authoritative Task returned by POST /claim**, never the potentially stale
GET READY payload. The fresh claim response now also exposes the same
top-level claim ID that idempotent claims already expose, preserving
older callers.

Deterministic tests cover malformed/missing IDs and task identity,
claim denial (including HTTP 422), stale READY prompt suppression,
exact claim-to-completion fencing, and the fresh response shape. The
isolated complete-package overlay reported **30 passed** across these
and the existing recovery, shell and KV-cache tests. These are source
tests, not signed or production task completions.

Release remains HOLD: do not remove the server's legacy claimless
compatibility in production without a documented client census, genuine
Neo4j transactional stale-owner/lease negative tests, and owner-approved
service cutover. No live router/AssistX or task graph was changed.


## Heartbeat session retention (follow-up)

The legacy fleet worker omitted `session_id` in task heartbeat JSON, while
the graph heartbeat function constructed `props={"agent_session_id": None}`
and wrote it with `SET t += $props`. That could erase the session ID
already stored by the authoritative claim. The worker now forwards the
original exact session ID with `claim_id` on every heartbeat. For other
legacy callers, `Neo4jClient.heartbeat_task` leaves `agent_session_id`
untouched when the optional parameter is absent; it still updates the
field when explicitly provided.

An isolated fake-session regression proves omitted session IDs never
remove the original binding. The expanded source-only suite reports
**31 passing tests** and one existing Pydantic warning. Full server-side
lease/claim fencing and old-client reconciliation remain P0 release holds.


## Productive completion is more than HTTP 200

The previous local LLM handler recorded `DONE` whenever the transport status
was HTTP 200, including `choices=[]` and empty or non-text message bodies.
The worker now requires a nonempty text generation in the first completion
choice and reports `FAILED` for HTTP-200 responses without a valid generated
answer. Eight parameterized negative tests plus existing valid generation
tests pass. This guards **task status quality**, not signed or independently
verified token usage.

Separately, the old worker discarded the HTTP response to
`POST /api/tasks/{id}/complete`; a rejected, missing, or mismatched
completion could be treated by the poller as finished work. The worker now
requires HTTP 200 and an authoritative `task.id` and terminal `task.status`
matching the exact submitted task and outcome; otherwise it returns no-work
and yields. Five negative cases were tested, including transport failure,
HTTP 409, empty response, mismatched task ID, and nonterminal status.
The client does **not** blindly repeat a model inference or completion after
an ambiguous response; independent post-commit reconciliation is a separate
requirement.

Expanded isolated regression suite: **44 passed** (one preexisting Pydantic
warning). Source-only, not deployed, and still under the same P0 release holds.
