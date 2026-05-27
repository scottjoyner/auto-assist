# AssistX Status - May 27, 2026

## Current State

AssistX is the task-state authority and Sophia ingestion target. The approved
release path is **Plan B: stabilize and cut over through Paperclip**. Paperclip
runs locally and the `hermes_local` adapter is registered, but cutover has not
passed its completion canary. The direct `hermes-agent-adapter.service` poller
must remain enabled until it does.

### Verified

- Sophia secured enrollment supports one-time verification proofs and an operator override.
- Sophia container configuration targets the shared `assistx` Neo4j database.
- Paperclip is running as a user service and has a registered `hermes_local` adapter.
- A signed Sophia-originated canary created canonical graph/task/dispatch records and Paperclip issue `ASS-14`.
- After the Paperclip wrapper repair, `ASS-14` reached `done` and recorded a completion comment through `hermes_local`.
- AssistX automatic Sophia dispatch now creates Paperclip issues through an initialized client and retains successful run output even after Paperclip clears live run pointers during escalation.
- Paperclip-dispatched tasks are reserved from the legacy direct worker claim path while rollback remains enabled.
- A live unauthenticated enrollment request is rejected (`401`), and the removed legacy `/admin/voiceprints/enroll` endpoint returns `404`.
- Post-reservation canary `ASS-15` created one canonical dispatch and `MediaCapture {origin: "sophia_voice"}` while the still-running direct poller remained idle.
- Sophia task ingestion now links `Intent -> Task` before external dispatch, eliminating the race that let the intent orchestrator create a second Paperclip issue.
- The Paperclip Hermes agent is aligned to the loaded LM Studio model, and its scheduled heartbeats are disabled with single-run concurrency for issue-driven cutover operation.
- Controlled canary `ASS-20` created one canonical capture, task, and Paperclip issue while the direct poller remained idle.
- The MacBook Air LM Studio service is reachable over Tailscale and exposes small local models, including `qwen3.5-0.8b`.
- AssistX now treats the MacBook Air as an optional low-risk draft endpoint only; it is not an execution worker or a dependency of the Paperclip cutover.
- An authenticated AssistX draft canary registered and probed `scotts-macbook-air.lmstudio` (`4` models online), then generated one harmless sentence with `qwen3.5-0.8b`.
- A bounded Paperclip-only diagnostic found and repaired structured `plain` environment bindings being passed to Hermes as object strings; `ASS-22` then reached `done` with a successful terminal run in about 27 seconds using the temporary Mac-backed model.
- Signed canary `ASS-23`/`ASS-24` exposed one remaining duplicate-dispatch race; inline-handled voice intents are now marked orchestrated atomically and completion events update their linked AssistX task state.
- Post-repair signed canary `ASS-26` created one canonical capture, linked task, and Paperclip issue, completed through Hermes with a successful terminal run, and synchronized `Dispatch=COMPLETED` and `Task=DONE` in AssistX.
- A replay of the pre-idempotency `ASS-26` source event identified a legacy-dispatch migration edge and was cancelled as `ASS-27`; retries now reuse pre-key dispatches and cannot reopen terminal tasks.
- Final post-fix signed canary `ASS-28` created exactly one Paperclip issue, completed and synchronized `Task=DONE`, and a signed replay returned the same task without creating another issue or changing terminal state.
- After the bounded diagnostics, the live Paperclip adapter was restored to `qwen/qwen3.6-35b-a3b`; the MacBook Air remains draft-only in the release architecture.

### Active Blocker

On May 26, 2026, the repaired signed canary `ASS-14` reached `done`, but its
`hermes_local` run then timed out after 300 seconds rather than exiting
normally. A fresh post-reservation canary, `ASS-15`, again timed out after 300
seconds without a completed disposition; its synthetic continuation run and
issue were cancelled during cleanup. Its assignment output contains no API
tool call or disposition attempt before timeout. Earlier canaries also demonstrate that
missing issue dispositions can fall through to a broken legacy process-backed
`hermes-local` recovery agent. Cutover remains blocked until a canary completes
with a successful terminal run, not merely a `done` issue status.

After aligning the Paperclip agent model to the loaded Hermes/LM Studio model,
`ASS-16` exposed scheduled heartbeat runs attempting to mutate the active
assignment owned by a different run ID. Scheduled Hermes heartbeats are now
disabled and concurrency is limited to one. A clean follow-up, `ASS-20`,
created exactly one dispatch but its issue-assigned `hermes_local` run did not
produce a disposition before it was cancelled during canary cleanup.

On May 27, a bounded diagnostic temporarily pointed `hermes_local` at the
MacBook Air model and exposed a separate adapter defect: stored environment
bindings such as `LM_BASE_URL` were passed to the process as `"[object Object]"`.
The Paperclip adapter now unwraps `plain` bindings before process execution,
and diagnostic issue `ASS-22` reached `done` with a successful terminal run.
The first signed follow-up then exposed a still-open AssistX race, producing
two Paperclip issues before the intent/task link became visible to the
orchestrator. Inline voice task intents are now marked orchestrated in the
same database write that creates or updates the intent, and completed
Paperclip events now close the linked AssistX task.

Signed canaries `ASS-26` and `ASS-28`, using the temporary Mac diagnostic
worker, completed the full capture/task/issue/run/synchronization chain with
one issue and a successful terminal run. `ASS-28` was also replayed after the
retry repair and retained its single issue and terminal task state. The
temporary execution configuration was then removed. Direct x1 tests remain
too slow for release confidence:
`google/gemma-4-31b` took about 48 seconds and `qwen/qwen3.6-27b` about 29
seconds for an eight-token probe, producing reasoning but no answer text.
The remaining cutover blocker is selecting an operationally viable production
worker model and completing the authorized enrollment canary.

### Release Architecture

| Component | Release role |
|----------|--------------|
| Sophia | Realtime voice/auth edge; sends signed non-realtime work to AssistX |
| AssistX | Canonical ingestion, graph/task authority, Paperclip dispatch/synchronization |
| Paperclip | Non-realtime execution route for this release |
| `hermes_local` | Supported Paperclip adapter |
| Direct poller | Rollback path; remains enabled until canaries pass |
| MacBook Air draft endpoint | Optional advisory drafting for operator-invoked low-risk text only |
| Swarm/direct worker claiming | Deferred follow-up; not part of cutover |

### Next Steps

1. Decide whether to tune or replace the slow x1 production inference model for the cutover worker; keep the MacBook Air outside automatic execution unless a separate operational decision explicitly promotes it.
2. Repeat the signed-ingest canary using that selected production configuration; `ASS-28` already proves the integration chain and retry behavior with a bounded diagnostic worker.
3. Perform authorized operator enrollment with a real enrollment sample so audit data is meaningful.
4. Benchmark the registered Mac small models only for non-sensitive draft quality and latency while it remains an advisory lane.
5. Rotate previously committed/local development secrets and keep only environment templates in source control.
6. Only after a production-worker completion and enrollment verification, disable `hermes-agent-adapter.service`.

### Deferred Work

- Direct worker claiming and distributed/fleet routing.
- Model endpoint probing as an execution selection mechanism; current probing supports inventory and draft canaries only.
- Paperclip deprecation or demotion to an optional mirror.

The draft proposal in `docs/plans/messaging-trigger-fundamental-changes.md`
suggests switching local execution to the direct worker to avoid the Paperclip
timeout. That is not adopted for the current release: it would reintroduce a
second execution authority before the existing dispatch path is diagnosed.

### Verification

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_paperclip_poller.py tests/test_paperclip_client.py tests/test_swarm_phase2.py tests/test_migration_api.py tests/test_outbox_client.py tests/test_intent_orchestrator.py
PYTHONPATH=src .venv/bin/pytest -q tests/test_draft_model.py
```
