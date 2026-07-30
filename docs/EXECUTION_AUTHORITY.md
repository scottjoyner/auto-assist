# Execution and mutation authority

AssistX previously had Paperclip and a direct Hermes poller consuming work in
parallel. Both integrations remain available, but they must share the canonical
Neo4j task lifecycle and a deployment must make its execution choice explicit.

## Runtime backend

`EXECUTION_BACKEND` controls worker pollers:

| Value | Paperclip poller | Direct Hermes poller | Guidance |
|---|---:|---:|---|
| `paperclip` | yes | no | Paperclip owns non-realtime execution |
| `direct` | no | yes | Fenced Hermes workers claim tasks directly |
| `auto` | yes | yes | Compatibility only; avoid for overlapping task populations |

`auto` preserves legacy behavior and is not a safe substitute for deciding
which backend owns production work. If both integrations run, task eligibility,
reservation, and idempotency policy must prove that they cannot execute the
same work.

## Authority matrix

| Action | Allocator | Worker/node | Controller | Authenticated operator |
|---|---:|---:|---:|---:|
| Recommend node/model | yes | no | no | view |
| Reserve allocation | yes/API | no | reconcile expiry | release |
| Claim and execute task | no | current target only | reconcile stale state | no |
| Heartbeat/complete | no | current claim ID only | no | no |
| Request preemption | policy | observe/acknowledge | reconcile | yes |
| Write checkpoint | no | current claim ID only | validate/handoff | no |
| Diagnose incident | advisory | report observations | yes | view |
| Approve recovery fingerprint | no | no | no | yes |
| Execute recovery | dispatch only | signed typed runbook only | reconcile | enable/disable |
| Propose code change | proposal only | no | no | yes |
| Approve improvement proposal | no | no | no | yes |
| Produce code evidence | no | bounded worker | verify/record | review |
| Promote exact patch | no | no | no | yes |
| Commit, push, open PR | no | no | no | normal release workflow |

## Required fences

- Allocation must be reserved against a recent snapshot before it constrains a
  claim.
- Worker state changes must carry the current claim ID.
- Controller writes must carry the current lease fencing token.
- Recovery instructions must have a valid signature, TTL, typed action, and
  configured node alias.
- Improvement acceptance must use executor-measured, signed evidence.
- Promotion must use the exact patch SHA-256, accepted attempt, original base
  HEAD, clean target, and successful post-apply verification.

These fences are independent. A healthy node is not automatically authorized
to recover a service, and a verified code attempt is not automatically
authorized for promotion or release.

## Deployment rule

Before enabling a backend or mutation path, record:

1. the owning process and unique node/controller identity;
2. the eligible task population;
3. the secrets and allowlists it receives;
4. its idempotency and fencing boundary;
5. its disable and rollback procedure;
6. the canary that proves the boundary.

Use [`fleet-recovery-rollout.md`](fleet-recovery-rollout.md) and
[`self-improvement-rollout.md`](self-improvement-rollout.md) for the two
mutation-capable paths.
