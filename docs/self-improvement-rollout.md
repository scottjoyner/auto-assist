# Self-improvement rollout

This runbook enables bounded repository improvements on selected nodes. Start
with one non-critical repository and one code-capable node. Do not distribute a
shared fleet-wide signing identity.

## 1. Prerequisites

The control plane and worker need:

- access to the same Neo4j task state;
- a Git checkout at a known base branch;
- a dedicated writable worktree parent outside that checkout;
- enough storage for detached worktrees and verification artifacts;
- the repository's existing test dependencies;
- a unique node identity and HMAC secret;
- authenticated operator access to `/operations`;
- `FLEET_UNSAFE_SHELL_TASKS_ENABLED=false`.

If the worker runs in a container, bind-mount both the repository and dedicated
worktree parent. The path stored in `ASSISTX_REPOSITORY_ROOTS_JSON` must be the
path visible inside that process, not the host-only path.

Do not mount signing secrets into an untrusted model runtime. The AssistX
executor retains the secret and strips it from the model subprocess
environment.

## 2. Configure one worker

Create a random secret through the deployment's secret manager. Give each node
a distinct key ID and secret:

```env
ASSISTX_REPOSITORY_ROOTS_JSON={"auto-assist":"/srv/repos/auto-assist"}
ASSISTX_IMPROVEMENT_WORKTREE_ROOT=/var/lib/assistx/improvement-worktrees
ASSISTX_IMPROVEMENT_VERIFY_TIMEOUT_SECONDS=120
ASSISTX_IMPROVEMENT_ATTESTATION_KEY_ID=xwing-improvement-v1
ASSISTX_IMPROVEMENT_ATTESTATION_SECRET=<node-specific-random-secret>
FLEET_UNSAFE_SHELL_TASKS_ENABLED=false
```

Create the worktree directory with ownership that permits the worker process to
create and remove child directories. Keep it outside all configured repository
roots.

The worker needs Git and the verification executables required by approved
contracts. It does not need GitHub credentials because the bounded cycle cannot
push or open a PR.

## 3. Configure the control plane

Register the worker's key ID with the matching secret:

```env
ASSISTX_REPOSITORY_ROOTS_JSON={"auto-assist":"/srv/repos/auto-assist"}
ASSISTX_IMPROVEMENT_WORKTREE_ROOT=/var/lib/assistx/improvement-worktrees
ASSISTX_IMPROVEMENT_VERIFY_KEYS={"xwing-improvement-v1":"<same-node-secret>"}
FLEET_UNSAFE_SHELL_TASKS_ENABLED=false
```

The process that performs promotion must see the configured repository at the
captured base HEAD and have permission to modify it. Worker and control-plane
paths may differ between hosts, but each process's repository alias must
resolve to its local checkout.

When multiple node keys are trusted, register them all in the JSON map. A key
ID identifies one trust relationship; do not reuse it with different secrets.

## 4. Readiness checks

Restart the configured processes and inspect:

```bash
curl -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  http://localhost:8000/api/fleet/operations-readiness
```

The optional checks should show:

- `improvement_repositories`: at least one alias;
- `improvement_attestation`: at least one trusted node key;
- `improvement_worktrees`: a configured dedicated root;
- `legacy_shell`: ready/disabled.

Readiness reports configuration presence. Also verify manually on the selected
worker:

```bash
git -C /srv/repos/auto-assist rev-parse --show-toplevel
git -C /srv/repos/auto-assist status --short
test -d /var/lib/assistx/improvement-worktrees
test -w /var/lib/assistx/improvement-worktrees
```

The promotion checkout must be clean before promotion. The worker's base
checkout may be dirty during isolated execution, but a clean canary baseline
makes diagnosis easier.

## 5. Canary sequence

Choose a harmless behavior with one or two exact files and a fast focused test.
Do not begin with deployment, authentication, schema, lockfile, or recovery
code.

Create the proposal:

```bash
curl -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  -H 'content-type: application/json' \
  -d '{
    "title": "Improvement canary",
    "repository": "auto-assist",
    "objective": "Make the selected fixture wording clearer without changing behavior.",
    "allowed_paths": ["tests/fixtures/improvement_canary.txt"],
    "verification_commands": [["pytest", "-q", "tests/test_improvement_runtime.py"]],
    "recommended_tier": "tool-small",
    "priority": "LOW"
  }' \
  http://localhost:8000/api/fleet/improvement-cycle/proposals
```

Review the returned contract, then approve its task:

```bash
curl -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  -X POST \
  http://localhost:8000/api/tasks/<task-id>/approve-proposal
```

In Operations, confirm:

1. the selected worker owns the current claim;
2. the attempt completes from an isolated workspace;
3. every changed path is in contract;
4. executor verification passed;
5. the signature key ID matches that worker;
6. the attempt is accepted and its learning profile changed;
7. the base repository did not change during execution.

Before promotion, independently inspect the attempt summary and exact
fingerprint. Confirm the promotion checkout is clean and still at the captured
HEAD. Paste the fingerprint and a meaningful reason in Operations, or call:

```bash
curl -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  -H 'content-type: application/json' \
  -d '{
    "fingerprint": "<exact-64-character-patch-sha256>",
    "reason": "Canary evidence, scope, and focused verification reviewed."
  }' \
  http://localhost:8000/api/fleet/improvement-cycle/attempts/<attempt-id>/promote
```

Inspect `git diff` and rerun the focused test locally. The expected state is a
verified but uncommitted patch. Revert or carry it into the ordinary
commit/push/PR process according to the canary plan.

## 6. Expansion criteria

Add repositories, nodes, and stronger tiers one at a time. Expand only after:

- accepted and rejected attempts are both classified correctly;
- invalid signatures and wrong paths fail closed;
- a failed verification creates no promoted residue;
- repair tasks stay `PROPOSED` and stop at the iteration limit;
- migration invalidates the old claim;
- Operators can explain every placement and promotion decision;
- cleanup leaves no unexplained worktrees.

Repository-specific contracts may select narrower test commands, but must not
expand the global executable allowlist from a task payload.

## 7. Key rotation

Rotate without invalidating in-flight evidence:

1. Generate a new node-specific secret and key ID.
2. Add the new ID/secret to `ASSISTX_IMPROVEMENT_VERIFY_KEYS`.
3. Restart or reload the control plane and confirm readiness.
4. Change the worker's attestation key ID and secret.
5. Restart the worker and run a canary that records the new key ID.
6. Wait until no in-flight attempt uses the old ID.
7. Remove the old ID from the verification map.

Never change a secret while retaining the same key ID; that makes audit and
in-flight verification ambiguous.

## 8. Disable and rollback

To stop new improvement execution:

1. Stop or drain code-capable workers from `bounded_code_change` tasks.
2. Leave unapproved proposals in `PROPOSED` or cancel them through the normal
   task control.
3. Do not remove a verification key while an attempt using it is in flight.
4. Inspect any `PROMOTING` attempt before retrying; promotion claims become
   reclaimable after ten minutes.
5. Set `ASSISTX_REPOSITORY_ROOTS_JSON={}` on processes that must no longer
   resolve repositories.
6. Remove worker attestation secrets only after active claims are closed.

A promotion whose post-apply checks fail attempts an automatic reverse apply.
If its result says `rolled_back=false`, stop promotion work, preserve the
checkout and evidence, and resolve the repository manually before making it
clean.

## 9. Troubleshooting

| Symptom/reason | Meaning and action |
|---|---|
| `repository_root_not_configured` | Add the exact alias to the process-local repository map. |
| `repository_head_drifted` | The promotion checkout moved after execution; review and rerun a new attempt on the new HEAD. |
| `promotion_target_not_clean` | Preserve/review local work; make the target clean intentionally before retrying. |
| `executor_attestation_key_unknown` | Register the worker key ID on the control plane. |
| `executor_attestation_invalid` | Stop the node, compare secret deployment and key ID, and treat the evidence as untrusted. |
| `patch_fingerprint_mismatch` | Recopy the signed exact fingerprint; do not promote a visually inferred patch. |
| `promotion_patch_outside_contract` | Reject the attempt and inspect the worker because the portable patch escaped scope. |
| `promotion_verification_failed` | Inspect verification and `rolled_back`; do not reapply without a new reviewed attempt. |
| `isolated_worktree_already_exists` | A prior execution likely crashed before cleanup; inspect that workspace before removal. |

After a crashed attempt, use `git worktree list` from the configured repository
to identify the detached workspace. Preserve anything needed for incident
analysis, then remove only that exact path:

```bash
git -C /srv/repos/auto-assist worktree list
git -C /srv/repos/auto-assist worktree remove --force \
  /var/lib/assistx/improvement-worktrees/<exact-workspace-id>
git -C /srv/repos/auto-assist worktree prune
```

Do not recursively clear the worktree root. It may contain active attempts from
other tasks.

## 10. Evidence and retention

Raw patches live in the durable attempt evidence and are intentionally omitted
from list/status responses. Access should be limited to operators who can
review and promote repository changes. Back up Neo4j according to the control
plane's recovery policy, and define retention for old attempts before scaling
the loop. Cleanup policy must preserve audit fields needed to explain model
placement, rejection, repair, and promotion.
