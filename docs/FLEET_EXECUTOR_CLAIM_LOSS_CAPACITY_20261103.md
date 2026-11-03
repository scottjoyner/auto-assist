# Source-only fleet executor: claim-loss capacity fence

## Live motivation

AssistX approved work supply is still zero because the canonical signed
fleet projection expired. A source read of `fleet_executor.py` nevertheless
revealed an important **future-admission safety defect**: after a heartbeat
rejects a Task claim, the code signaled cancellation but waited only ten
seconds for the router invocation thread before returning and clearing its
`_active` slot. An injected router runner that ignores cancellation could
continue inference even as the scheduler advertises freed capacity.
Exceptions raised by the heartbeat path could also free the slot while
inference remains active. The subprocess runner's terminate/kill grace did
not verify the child actually exited before freeing the slot.

## Narrow fail-closed change

- After an explicit lost claim, signal cancellation and retain the active
  Task slot until its invocation thread really exits. A configurable test
  grace avoids slow tests, but does not weaken the production fail-closed
  indefinite wait after grace. No new Task is claimed while occupancy remains.
- For an exception/partition, **stop the invocation before attempting any
  terminal failure mutation**; always cancel and await invocation exit before
  cleaning up the active slot. Never write a `DONE` completion on a lost claim.
- A supervised router subprocess is terminated, then killed if needed, and
  must be **actually exited** before returning. A child that ignores both
  bounded grace waits retains the slot until process exit is proven.

The deliberate fail-closed indefinite join can reduce availability if an
uncancellable child never terminates, but prevents false free-capacity and
unbounded overlapping inference. Deployment must retain process watchdogs,
operator escalation, and resource limits; **no forced release** is justified
by elapsed timeout alone.

## Verification

Synthetic *disposable* in-memory Neo4j clients and non-cooperative runner
fixtures prove claim rejection and heartbeat exception keep the slot busy,
reject duplicate claims, avoid early terminal mutation, and eventually free
it only after the blocked invocation is released. A fake child demonstrates
that terminate and kill timeouts cannot be mistaken for process exit.
No live Task was claimed, no real subprocess was killed, and production
services remain unchanged.

## Remaining P0 release holds

Client-side cleanup does **not** supply authoritative claim revocation or
side-effect fencing. P0 [#428](https://github.com/scottjoyner/auto-assist/issues/428)
requires transactional Task ID/owner/session/generation/lease/claim checks
including attempt zero and replay negatives, plus source-owned client census.
The canonical runtime projection remains expired with zero signed slots;
five active legacy node agents still send unauthorized polls. This source
branch is intentionally draft and not a production authorization.
