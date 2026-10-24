# Hermes physical process-group revocation — October 22, 2026

**Decision: production HOLD, draft PR #425, P0 #414 OPEN.** Fleet Commander performed a strictly disposable, offline execution-fencing rehearsal on x1-370; it did **not** modify the real Hermes container, mount layout, private files, signing credentials, NAS storage, provider routes, or active tasks.

## Gap addressed by this rehearsal

Earlier synthetic container tests exercised the real patched strict executor, but represented all child processes with mocks. Those tests proved claim/JWT validation, heartbeat-before-launch, loss-of-lease output suppression and callback ordering. They did **not** prove the Linux process group of a real subprocess and descendant was terminated.

The updated restricted-container test now creates a **real Python parent process** which creates a second harmless sleeping Python process. It is launched by the **actual patched `adapter.run_hermes`** from a fresh synthetic signed owner-bound claim, but a test-only Popen adapter substitutes `python -S -c` for the Hermes/model executable; **no model or provider is executed**. The child Python processes use `-S` to skip site/sitecustomize and do not inherit the synthetic executor token or mock OpenAI API key.

Inside the same non-root, network-disabled, read-only-root filesystem container, the parent writes only its and its child PID to a synthetic marker under the authorized writable `/workspace`. A separate bounded watcher validates that both PIDs exist and then revokes the local synthetic task lease. The descendant first installs a **SIGTERM-ignoring handler** and writes a separate readiness marker. Only after that marker is observed does the parent write the combined PID witness, making the test a deliberate *resistant descendant* rather than an easy cooperative exit. The **actual patched strict executor** observes revocation and uses its Linux process-group fencing path: SIGTERM for graceful shutdown, then SIGKILL for remaining members **even when the group leader has already exited**. The test verifies both PIDs are no longer running (absent or zombie), including the SIGTERM-resistant child, and that no output or session ID is authorized. Even on failures, a bounded finalizer attempts to terminate any remaining synthetic process group. The private marker is deleted.

This experiment runs on each of **two normal restricted Hermes entrypoint starts**, and the canary requires one explicit `HERMES_SYNTHETIC_REAL_PROCESS_GROUP_FENCED_PASS` marker per start. The new independently named check is `real_os_parent_and_descendant_process_group_fenced_in_image`.

**Physical result: 15/15 PASS**, with two normal entrypoint starts, a SIGTERM-resistant descendant and a required ready-after-signal-handler witness, including all previous signed-corpus, rollback/owner, exact patched-image, RO/RW mount, no-network, no-real-provider, restart, and disposable Docker cleanup checks. The expanded focused unit suite must be evaluated separately at the exact published commit. Production Hermes stayed running on image `sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307` with 16 legacy mounts.

## Important boundaries

This is real operating-system process-group termination, but **the lease revocation is simulated locally**. It does not prove an independently administered revocation authority, actual server-to-worker lease freshness under a partition, authenticated multi-user execution receipts, process behavior outside the restricted container, or rollback resistance. The real model subprocess can begin running before a lease failure is observed; this candidate bounds termination and suppresses the revoked task result, not retroactive side effects.

Production still needs a separately controlled, rollback-resistant high-water witness, approved signed read-only input and isolated writable scratch replacing all 16 legacy binds, signed executable/image provenance, real owner-bound task-grant and revocation/fencing tests, and verified release rollback. **Do not merge, deploy, or close #414 based on this rehearsal.**

## Process-group hardening in the tested runtime source

The existing `_terminate_process` helper had returned when its **session leader** exited after SIGTERM; this could leave a SIGTERM-ignoring descendant running in the same process group. The actual adapter now sends a bounded grace SIGTERM, then a group-wide SIGKILL even when `proc.poll()` already reports that the leader exited. Guardrails reject invalid process-group identifiers; unit tests cover reaped leaders, still-running leaders and missing groups. This change is limited to worker-started `start_new_session=True` subprocess groups, not arbitrary system processes. It cannot retroactively undo effects that occurred before revocation was observed.
