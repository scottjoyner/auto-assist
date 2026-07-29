# Bounded self-improvement cycle

AssistX treats repository improvement as an evidence-gated control loop, not as
an unrestricted agent prompt:

`PROPOSED contract -> approved task -> bounded tools -> verification envelope
-> central acceptance -> skill profile -> routing/repair`

## Work contracts

Create review-first work with
`POST /api/fleet/improvement-cycle/proposals`. A contract fixes:

- the repository and exact repository-relative files that may change;
- the model tier and corresponding file/diff budget;
- the only permitted tool phases: inspect, patch, verify, inspect diff;
- verification commands expressed as argv arrays, never shell strings;
- mandatory clean-worktree, review, and iteration limits.

`tool-small` work is capped at two files and 160 changed lines. Larger scopes
must be deliberately assigned to a stronger tier. Paths containing traversal
and verification executables outside the allowlist are rejected before a task
is created.

## Small-agent packet

The Hermes adapter turns the contract into a deterministic four-step packet.
It restricts the session to terminal, file, and code-execution toolsets and
requires a machine-readable completion envelope containing:

- exact changed files and total added/deleted lines;
- the ordered tools used;
- every required verification argv and its return code;
- a factual summary and optional next candidate.

Repository names are resolved through
`ASSISTX_REPOSITORY_ROOTS_JSON`, for example:

```env
ASSISTX_REPOSITORY_ROOTS_JSON={"auto-assist":"/home/scott/git/auto-assist"}
```

Before the model runs, the adapter requires a configured Git worktree and a
clean `git status`. After it runs, the adapter independently reads Git status
and numstat, validates every changed path, and executes the contract's
allowlisted argv commands with `shell=False`. Model-reported paths, diff sizes,
and return codes are discarded in favor of this executor evidence.

The claim ID is preserved through heartbeat and completion so a migrated or
stale Hermes execution cannot report a result for a newer attempt.

## Central acceptance

AssistX does not accept prose such as "done" as a code change. The completion
endpoint changes a reported `DONE` to `FAILED` when evidence is missing, a path
escapes the contract, the file/diff budget is exceeded, a tool phase is absent,
or a required verification command was not run successfully. Evidence must be
marked as executor-attested and confirm that the worktree was clean before
execution; self-reported model evidence is rejected.

Every managed attempt becomes an `ImprovementAttempt`. It updates an
`AgentSkillProfile` keyed by agent, model, and task family. The allocation
engine uses a smoothed verified-success rate for bounded code changes, allowing
small models that repeatedly pass real checks to earn more work while
unreliable models lose placement value.

## Repair and learning

A rejected attempt creates at most one idempotent, narrower, `PROPOSED` repair
task per iteration. The repair:

- carries the exact observed failure reasons as learned guidance;
- narrows the allowed scope to one file;
- escalates one model tier;
- remains inert until an operator approves it;
- stops after two learning iterations.

Current state is available from `GET /api/fleet/improvement-cycle` and appears
in Operations under **Agent learning profiles**.

This loop intentionally does not commit, push, open pull requests, use the
network, or approve its own repair work. Those remain separate operator or
release-controller decisions.
