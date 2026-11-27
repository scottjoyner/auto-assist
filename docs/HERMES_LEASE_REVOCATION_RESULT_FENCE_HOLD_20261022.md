# Hermes lease-revocation handoff and output-fencing rehearsal — 2026-10-22

**Release decision: HOLD.** This revision belongs to draft auto-assist PR #425, P0 issue #414. No production Hermes restart, service change, protected source read, mount update, real provider/model dispatch or externally issued task was performed.

## Concrete race closed

The strict executor already denied launch when a lease had been lost at the *start* of its function, and continuously checked its revocation Event while a process was running. However, it could still report `success=True` for a process that exited immediately if its lease was revoked inside the `subprocess.Popen` handoff, or after the final poll while collecting output. This could create a local success artifact even though server-side completion should already be fenced.

`src/assistx/strict_executor_adapter.py` now examines `_lease_lost` **immediately after registering the newly spawned child** to cover revocation during `Popen`. It terminates any still-running child and returns an `executor_lease_lost` denial. The result path checks `_lease_lost` again **after `communicate()`, including for a child whose return code is zero**. Any lease loss suppresses stdout/stderr and session ID and returns `success=False`. The existing running-process Event-fencing loop and authoritative task-completion denial remain active. This reduces race windows; **it cannot claim that a revoked process never began running** because revocation may happen inside an OS process-create call before the worker can observe it.

## Validation

Offline unit tests in `tests/test_hermes_launch_gate.py` cover:
- Revocation during `Popen` with an instantly exited fake child, then fail-closed zero-output denial.
- Revocation during output collection after a successful process exit; output and session ID suppressed.
- Revocation while a fake subprocess remains active, with forced termination and output denial.
- Prevention of server-authoritative task completion after revocation.
- Existing signed canonical owner, active task/claim/projection/model scope, token expiry/forgery, fresh heartbeat order and offline failure checks.

The disposable pinned exact-image research Compose test now includes a separate required check `revocation_during_spawn_and_result_fenced_in_image`. On each of TWO normal, rootless, networkless Hermes entrypoint starts, the **actual patched executor** receives synthetic signed owner claims, mocked successful heartbeats, and an explicitly fake process. One case revokes at fake process constructor; a second revokes during fake `communicate()`. Both must return `executor_lease_lost` and no output/session ID. A specific marker is checked once per start. The previous 13 restricted tests (kernel RO corpus/RW scratch, signed checkpoint replay, changed-owner denial, simulated heartbeat outage, persistence, and disposable Docker cleanup) also remain active.

**Physical result:** 14/14 restricted Compose checks passed on x1-370. The targeted combined pytest suite passed 51/51 cases before the full regression run. All sources/keys/token fixtures, images, containers and scratch are disposable and synthetic. Production Hermes continued running on the original image `sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307`.

## Remaining NO-GO

A fake process and local Event injection are not a live owner revocation, authenticated execution receipt, independent fencing witness or multi-user production API test. Production still has 16 bind mounts, nine writable, and contains protected SSD, mutable source, NAS-labelled local fallback and SSH-related mounts. Approved RO corpus and isolated RW scratch custody, independently administered monotonic high-water and key roots, production image provenance, owner-bound live-task receipts, partition/revocation acceptance and independently approved rollback remain missing.

**Do not merge or deploy PR #425 or close issue #414 based on this synthetic rehearsal.**

## Actual OS process-group revocation follow-up

The disposable networkless container now launches a harmless two-process Python group from the real patched executor and checks that synthetic lease revocation terminates both operating-system processes. The signed-token and fake-heartbeat evidence remains separate. **15/15 physical checks PASS**; see [HERMES_PHYSICAL_PROCESS_GROUP_FENCE_HOLD_20261022.md](HERMES_PHYSICAL_PROCESS_GROUP_FENCE_HOLD_20261022.md). Production HOLD unchanged.

## SIGTERM-resistant descendant qualification

The real child-process canary now waits for a separate descendant to **install SIGTERM ignore**, and the executor termination helper sends group-wide SIGKILL after the parent exits. The networkless physical test proves both parent and resistant descendant are no longer running. See [HERMES_PHYSICAL_PROCESS_GROUP_FENCE_HOLD_20261022.md](HERMES_PHYSICAL_PROCESS_GROUP_FENCE_HOLD_20261022.md). Real live tasks and independent custody remain HOLD.
