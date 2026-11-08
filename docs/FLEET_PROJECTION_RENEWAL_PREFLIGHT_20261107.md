# Fleet runtime projection renewal — independent witness, 2026-11-07

**RESEARCH / READ-ONLY / NON-ADMITTING.** The signed runtime projection remains
**HOLD**. This artifact does not approve a signer, extend a lease, create an
execution slot, update graph nodes, call a Task endpoint or authorize workers.

## Physical live results — x1-370 at 10:44 EST

- Running API and Neo4j containers: **healthy**; operator Task-list fixed and
  HTTP 200; machine identity-only on xwing HTTP 200 with `task_admission=HOLD`.
- Canonical graph `FleetProjectionState{name:'canonical'}`: generation **896**,
  approval expired approximately **408.82 hours** earlier.
- Fresh approved graph counts: **0/20 runtimes, 0/44 loaded-model instances,
  0/35 access paths, 0/2705 capacity observations**. **Correction:** the
  original `0/1 WorkSupplyState approved` count was a *diagnostic schema error*.
  `WorkSupplyState` has `generated_at_ts`, `allow_background`,
  `projection_generation` and `total_slots`, not `status='approved'` or
  `expires_at_ts`. A new read-only query finds the WorkSupply controller
  **fresh within 60 seconds**, with `allow_background=false`, zero slots and
  projection_generation `0`. The controller is healthy and refusing dispatch;
  it is not an expired or independent signed approval.
- Actual `x1-370` LM Studio model catalog contains one *physically loaded*
  model (`ornith-1.5-35b-a3b-apex-mtp`). Verified both API-container access
  `http://host.docker.internal:1234/v1/models` and native
  `http://127.0.0.1:1234/v1/models` return HTTP 200 and matching model ID.
- Bounded native model smoke used an **existing loaded instance**, two output
  tokens, HTTP **200**, and approximately **549 ms**. The smoke made no graph
  claims, admission change, model load, account/provider inference or work
  allocation.
- Additional physical inspection observed the actual `llama-server` process
  for the loaded model (PID `3602885` at snapshot), an ordinary non-symlink
  GGUF file of **23,718,411,552 bytes** and configured context **262,144**.
  A subsequent **read-only full-file SHA-256** of the exact backing GGUF
  completed in approximately **19.7 seconds** using idle-class I/O and
  process niceness 19, with inode, size and modification time unchanged
  between before/after file metadata checks. Full artifact SHA-256:
  `c83373e4c502c6d4929339406ffbb1f442598b153a3ade02b628752e00a282fd`.
  This is a concrete file-content fingerprint of the observed backing artifact,
  **not** a separately signed model attestation, immutable running image,
  concurrency allocation or Task-admission authorization.
- From inside the API container, pinned read-only model-list GETs succeeded on
  **three distinct addresses**—LAN `192.168.1.237:1234`, Tailscale
  `100.64.43.123:1234`, and Docker gateway
  `host.docker.internal:1234`—each returning HTTP 200 and the same model ID.
  This demonstrates path reachability, **not** independent outage failover,
  approved access-path custody, signed slot capacity, resource fencing,
  rollback, or fresh owner approval.

## New bounded physical capacity evidence — x1-370 2026-11-07 evening

A strict opt-in local-only `scripts/fleet-local-capacity-nonadmitting-canary.py`
verifies exactly one already loaded model and sends one single + two concurrent
synthetic chat requests, each capped at **2 completion tokens**. Its native
physical run returned HTTP **200** with exactly two output tokens on all three
requests, in **792, 765, and 748 ms** respectively; the loaded model identity
was unchanged on post-check. An earlier independent ad-hoc physical baseline
also passed **505 ms solo** and **781/784 ms concurrently**, all HTTP 200.
The earlier node telemetry observed `MemAvailable` near **20 GiB**, SwapFree
around **1 MiB** and a model resident RSS increase of about **189 MiB** after
three requests. These values are observational snapshots, **not** safe
production headroom for longer requests, multi-user work or a guaranteed
2-slot concurrency reservation. No process/service was restarted, no Task was
claimed, and no provider account was billed.

Safe repeat command **only on the existing x1-370 loopback where the exact
model is already loaded**:

```bash
python3 scripts/fleet-local-capacity-nonadmitting-canary.py \
  --live --model-id ornith-1.5-35b-a3b-apex-mtp
# Exit 2 is REQUIRED even if all three local generations passed.
```

The script accepts no alternate origin, no remote URL, no extra output-token
budget and no task identifiers. The ceiling is **64 output tokens per request**
using the fixed short prompt `Reply OK.`. The previous two-token witness
returned HTTP 200 and two **reasoning-only tokens** with empty final content,
and a longer canary prompt also consumed 64 tokens without an answer. Neither
is valid productive-output evidence. Identical fixed prompts may be cached;
three successful client requests do not prove physical server concurrency. It caps HTTP response sizes, disables redirects
and proxies, fails on a changed/unloaded model, emits no generated content,
and always returns `task_admission=HOLD` with `approved_parallel_slots=0`.
This canary proves bounded short-request responsiveness and that a *pair
of client requests completed* using a two-worker pool. It **does not prove
server-side simultaneous inference**, since the model server may queue the
requests internally. The machine-readable fields keep the old
`physical_parallel_pair_observed=false` and explicitly set
`server_parallel_processing_verified=false` until independent slot traces
exist. The tool also rejects zero-token, blank-content and mismatched-model
responses. **Sustained concurrency, exact memory limits, rollback and
resource-fenced claim/COMMIT remain unverified**.

## How to collect again (no credentials displayed)

Inside the approved x1-370 source checkout, pipe the script into the already
running API container. It uses only the API's existing read-only graph client
and one pinned local model-catalog GET; no environment values or unbounded
model response are emitted:

```bash
docker exec -i assistx-api python3 - \
  < scripts/fleet-projection-readonly-preflight.py
# Exit 2 = intentional HOLD regardless of snapshot contents.
```

The resulting machine-readable JSON is categorical and contains only:
canonical generation/expired hours, six fixed count summaries, model IDs,
failed reasons, and `task_admission=HOLD`. No node token, approval ID, signer
material, credential, Task payload or graph row is returned. A dedicated,
permission-limited GitHub workflow runs the source-level synthetic
regressions for both the graph preflight and opt-in local capacity canary.

## Why not renew approval in place

The canonical approval is not the only stale object. The actual runtime,
loaded-model, access and capacity evidence also expired. WorkSupplyState
*does* contain fresh controller state, but it correctly reports zero slots,
`allow_background=false` and generation zero while projection 896 is expired.
It cannot confer Task authority. The official publisher
`scripts/approve-runtime-projection.py --apply` would write to Neo4j and
first retire *all* runtime/model/access/capacity approvals. Running it with
stale manifests, guessed `approved_by`, fabricated evidence_refs or an
unverified model fingerprint would violate the monotonic, owner-reviewed
admission contract. Its stated max TTL is 900 seconds: a one-off approval
alone is not a durable scheduler or indefinite grant.

## Next legitimate positive slice

1. Capture the **actual** resident model process and model-file fingerprint,
   exact runtime version, physically reachable LAN and Tailnet access from the
   consuming API/worker context, quantitative memory/concurrency capacity,
   bounded one-model inference canary, rollback and a separate ownership
   witness. Distinguish local physical residency from stale graph approvals.
2. Validate a complete **non-admitting** runtime source profile with
   `scripts/build-runtime-projection-candidate.py`; preserve real external
   gates (`physical_runtime_identity`, `model_artifact_fingerprint`,
   `container_path_reachability`, `lan_preference_and_tailscale_fallback`,
   `shared_slot_admission`, `rollback_canary`). Never invent a canary PASS.
3. Obtain a fresh explicit operator approval with a new approval ID,
   generation **897** only if canonical head is still **896**, and a bounded
   TTL, then run the existing approval script in its **dry-run** mode first.
4. Only after independent signer and resource checks, apply through the
   protected graph transaction. Verify signed v2 projection, WorkSupplyState,
   exact provenance and one-node claim/COMMIT fencing before releasing any
   real Task. No auto-reset of `expires_at_ts` and no Basic-auth fallback.

Until all gates pass, `task_admission=HOLD` and machine legacy Task routes 403
are the correct operational result.

## November 7 capacity-evidence integrity correction (production admission HOLD)

The originally merged local LM Studio preflight unconditionally reported
`physical_parallel_pair_observed=true` after submitting two HTTP client calls.
That was an **unsupported physical concurrency claim**: the server may have
queued those two requests. The corrected tool preserves the legacy field but
sets it to `false`, records `client_parallel_requests_completed=true` when
both client requests complete, and explicitly sets
`server_parallel_processing_verified=false` until independent server-side
slot observations exist. The tool can never issue an approval.

The original **two-output-token** canary also accepted a valid HTTP 200 with
two reasoning-only tokens and **no final user-visible content**. The strict
replacement rejects zero token counts, blank/non-text final content, and an
unexpected served model ID. An actual Ornith-1.5-35B-A3B-APEX-MTP canary with
the longer synthetic prompt failed to produce final text within 64 output
tokens. Therefore, the corrected canary uses the fixed `Reply OK.` prompt,
retains a **64 output-token request ceiling**, and requires a *nonblank final
content string*; neither model-generated reasoning nor the content itself is
logged. A stale hard-coded two-token validation cap was identified during
live validation and replaced by the same configured 64-token maximum.

**Final real physical evidence**, from already-loaded `x1-370` LM Studio:
three requests returned HTTP 200 with **30 completion tokens each** and
nonblank final answers, at **2724 / 3368 / 3446 ms**. The last two were
submitted from a two-worker client pool, which is **not proof** of server-side
simultaneous processing. No model was loaded/unloaded, no Task API was called,
no graph node was written, and no signer state was changed. The tool retained
`approved_parallel_slots=0`, `owner_signed_projection_verified=false`,
`rollback_verified=false`, `task_admission=HOLD`, and intentional exit code 2.

The rewritten tests (35 focused tests) require a valid **30-token** final
answer to be accepted and verify rejection of zero, boolean, fractional,
negative, oversized, blank, non-string and wrong-model completions. Failures
emit only **allowlisted categorical error codes**, never arbitrary exception
messages that might include private user data or credentials. These are
capacity-observation improvements only; separate owner approval, fresh
signed resource evidence, independently verified server-side parallel slot
trace, realistic contention and a supervised Task rollback remain necessary.
