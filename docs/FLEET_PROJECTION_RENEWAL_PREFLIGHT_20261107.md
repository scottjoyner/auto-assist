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
  0/35 access paths, 0/2705 capacity observations, 0/1 WorkSupplyState**.
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
permission-limited GitHub workflow runs 14 synthetic regression tests.

## Why not renew approval in place

The canonical approval is not the only stale object. The actual runtime,
loaded-model, access and capacity evidence also expired and WorkSupplyState
has no fresh approved result. The official publisher
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
