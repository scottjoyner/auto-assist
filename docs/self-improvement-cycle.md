# Bounded self-improvement cycle

AssistX treats repository improvement as an evidence-gated control loop, not
an unrestricted agent prompt:

```text
PROPOSED contract -> operator approval -> fenced worker claim
-> isolated worktree -> bounded tools -> executor verification
-> signed evidence -> central acceptance -> skill profile
-> optional repair proposal -> exact operator promotion
```

## Work contract

`POST /api/fleet/improvement-cycle/proposals` creates a review-first
`PROPOSED` task. The request fixes the repository alias, exact
repository-relative paths, model tier, objective, priority, and verification
commands:

```json
{
  "title": "Harden parser edge case",
  "repository": "auto-assist",
  "objective": "Reject an empty parser token and cover it with a unit test.",
  "allowed_paths": [
    "src/assistx/parser.py",
    "tests/test_parser.py"
  ],
  "verification_commands": [
    ["pytest", "-q", "tests/test_parser.py"]
  ],
  "recommended_tier": "tool-small",
  "priority": "MEDIUM"
}
```

Commands are argv arrays, never shell strings. Supported verification
executables are defined by `ALLOWED_VERIFICATION_EXECUTABLES` in
`src/assistx/improvement_cycle.py`; a task cannot broaden that global list.
Absolute paths, traversal, control characters, unknown tiers, excessive paths,
and unknown executables are rejected before task creation.

Tier budgets are:

| Tier | Maximum files | Maximum added + deleted lines |
|---|---:|---:|
| `tool-small` | 2 | 160 |
| `reasoning-mid` | 5 | 500 |
| `reasoning-large` | 10 | 1,200 |

All tiers also have a 512 KiB portable patch limit, two learning iterations,
required review, an isolated worktree, and signed attestation.

An authenticated operator moves the proposal to `READY`:

```text
POST /api/tasks/{task_id}/approve-proposal
```

The agent cannot call this approval from its bounded work packet.

## Small-agent work packet

The Hermes adapter converts the contract into four deterministic phases:

1. inspect only allowed files;
2. apply one bounded patch;
3. run the required verification commands;
4. inspect and summarize the diff.

The packet forbids network access, path expansion, dependency changes unless
explicitly allowed, commits, pushes, pull requests, and success claims without
verification.

This structure lets a small model contribute inside a scope it can reliably
handle. It does not trust the model to police that scope.

## Isolated execution and evidence

Repository aliases resolve only through `ASSISTX_REPOSITORY_ROOTS_JSON`.
Before invoking the model, the worker:

1. resolves the configured Git repository and captures its base HEAD;
2. creates a detached per-task worktree under
   `ASSISTX_IMPROVEMENT_WORKTREE_ROOT`;
3. gives the model only the bounded packet;
4. independently reads Git status and numstat;
5. rejects paths or diff size outside the contract;
6. runs the allowlisted argv checks with `shell=False`;
7. exports a binary-capable patch up to 512 KiB;
8. signs the complete evidence envelope with the node key;
9. removes the isolated worktree.

The operator's base checkout may contain unrelated work while an attempt runs;
the detached attempt remains isolated. Promotion later requires that base
checkout to be clean and still at the captured HEAD.

Model-reported changed files, counts, and return codes are informational only.
Central acceptance uses the executor's observations. The model subprocess does
not receive the attestation secret, verification-key registry, repository-root
registry, or worktree-root configuration.

Evidence includes the repository, original HEAD, isolated workspace ID,
changed paths, diff counts, ordered tool phases, verification results, patch,
patch SHA-256, and HMAC attestation. List/status responses omit the raw patch.

## Central acceptance

A reported `DONE` becomes `FAILED` when the managed attempt lacks any required
property, including:

- valid node signature and known key ID;
- isolated execution and original HEAD;
- permitted changed paths and bounded file/diff/patch size;
- every required tool phase;
- a portable patch whose digest matches the signed digest;
- every exact verification command with a successful result.

Accepted attempts become durable `ImprovementAttempt` records. Each updates an
`AgentSkillProfile` keyed by agent, model, and task family. Allocation can then
use verified, smoothed performance instead of treating model size or a prose
claim as quality.

## Repair and learning

A rejected attempt may create one idempotent `PROPOSED` repair task for the next
iteration. The repair:

- carries the observed rejection reasons as learned guidance;
- narrows the allowed scope to one file;
- escalates one model tier;
- remains inert until an operator approves it;
- stops after the second learning iteration.

This is the self-reinforcing part of the loop: observed execution quality
changes later routing and produces a bounded follow-up. It does not grant the
system permission to keep editing indefinitely.

`GET /api/fleet/improvement-cycle?limit=100` returns attempts and learning
profiles. Operations displays them under agent learning and verified patch
candidates.

## Operator promotion

Only an accepted attempt can be promoted:

```text
POST /api/fleet/improvement-cycle/attempts/{attempt_id}/promote
```

```json
{
  "fingerprint": "64-lowercase-hex-characters-from-the-signed-patch",
  "reason": "Reviewed scope and verification evidence for release preparation."
}
```

Promotion is serialized with a durable `PROMOTING` claim. A claim abandoned
for ten minutes can be reclaimed. The promotion path:

1. revalidates the node signature and patch digest;
2. compares the pasted fingerprint in constant time;
3. requires the repository to remain at the original HEAD;
4. requires a clean promotion target;
5. rechecks every patch path against the contract;
6. runs `git apply --check`, applies, and reruns verification;
7. reverses the patch when verification fails;
8. records actor, reason, fingerprint, verification, and terminal status.

Terminal promotion state is `PROMOTED` or `REJECTED`. A successful promotion
leaves the reviewed patch uncommitted in the configured checkout. Commit,
push, PR creation, merge, and deployment remain normal human-governed release
steps.

## Non-negotiable invariants

- Proposal approval and patch promotion are operator actions.
- A worker can mutate only the current claim and its isolated worktree.
- A model cannot choose its repository root, signing key, executable allowlist,
  promotion target, or release action.
- Evidence is executor-observed and node-signed.
- Learning changes placement value and proposes bounded repairs; it never
  bypasses review.
- Failed verification cannot leave a knowingly promoted patch applied.

Deployment and incident procedures are in
[`self-improvement-rollout.md`](self-improvement-rollout.md).
