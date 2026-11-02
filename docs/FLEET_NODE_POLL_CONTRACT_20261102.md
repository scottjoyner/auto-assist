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
