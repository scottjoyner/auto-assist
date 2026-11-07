# Legacy fleet-agent 401/403 retry containment (research)

**HOLD — no productive machine Task authority granted.** This branch is stacked
on exact denied-machine parent #555 `63ce16f02d1a11be347f1cd83162e7dcf0183494`.
It does not deploy to any node, change credentials, modify Task/graph state,
or solve the expired signed projection and zero approved slots.

## Problem

Physical review found five older worker processes sending rejected operator-style
Basic credentials to the Task API (HTTP 401). The #555 node agent retained
legacy Basic fallback when machine identity mode was not configured, but its
GET `/api/agent/tasks` caller treated all non-200 statuses as an empty poll.
That caused repeat attempts every poll interval, including on HTTP 401/403.

## Containment implementation

- `_poll_once` accepts an optional notification callback for HTTP 401 and 403
  (after the source-enforced machine-token legacy Task denial check).
- `run` grows authorization-denial backoff exponentially with a bounded
  counter and **maximum 300 seconds**, while preserving a minimum one-second
  delay even with a zero/negative configured poll interval.
- A successful request or ordinary non-auth server error resets the denial
  counter. No auth response can become a Task claim or successful execution.
- Node-token/approved-origin mode remains forbidden from the legacy worker
  before Router POST, Task GET, Basic fallback, model call or graph access.
- Operator Basic compatibility is unchanged at source level, but must never
  be redistributed to fleet machine identities to suppress HTTP 401s.

## Validation and remaining gates

Focused source tests: `tests/test_fleet_agent_auth_denial_backoff.py` — 6 pass.
Independent Docker application image, `--network none --read-only`, source
mount read-only, Linux caps dropped: 53 pass across backoff, identity tests,
real-ASGI node API and five-route machine-denial coverage. No live HTTP or Task
calls; all credentials and node identities were synthetic.

This is a **source-only defense** against credential-rejection polling load.
The deployed five old workers do not yet run it. Physical rollout requires
reviewed staged immutable source and rollback, an approved single-node machine
principal and origin, legacy five-route 403s, fresh signed WorkSupplyState,
resource-authorized CAS/COMMIT and a supervised one-node canary. Do not merge
as an authorization bypass or flip background work on.
