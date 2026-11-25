# Fleet OpenCode Delivery 1/5 — Node Admission & Auth (2026-11-04)

Objective: Turn the known fleet-node-agent HTTP 401 blocker into a minimal, testable code change or a clearly supported HOLD. Current evidence from November 3: five nodes (xwing, Deathstar, Destroyer, Lenovo, OptiPlex) receive 401 on task-list using their per-node credentials; deployed list/claim path still expects general Basic auth; signed projection expired, zero approved slots. Production rollout remains HOLD.

Scope: This isolated auto-assist Git worktree only. Inspect the repository's effective current authentication/claim implementation and existing issue references; locate the smallest safe compatibility/identity boundary. Add narrowly scoped regression tests and, only if defensible, implement a minimal fix that preserves fail-closed semantics. If the security contract is ambiguous, document exact blockers rather than weakening auth.

Constraints:
- Do not access, print, copy, generate, rotate, or change actual secrets or node credentials.
- Do not restart services, change production, deploy, register nodes, relax gates, modify running leases, or modify other worktrees.
- No external paid models, purchases, or provider changes. Do not spawn subagents or parallel model calls.
- No force pushes, merges, branch deletion, commits to main, or PR creation. Stay on this branch.
- Preserve the existing HOLD: passing local tests never proves production admission.
- Keep tests targeted and resource-light; avoid memory-heavy suites and dependency installation.
- At most one independent implementation approach; stop after a narrow validation pass or on a blocker.
- Put a concise status in DELIVERY_RESULT.md: evidence, files changed, exact tests and results, risk, remaining gates, proposed PR description. Commit only if the code/tests are coherent and validated; otherwise leave draft changes with status.

Success: a credible scoped patch with exact test evidence and a fail-closed HOLD boundary, or a documented grounded blocker.
Machine-readable handoff for the staged delivery queue: DELIVERY_RESULT.md must contain a standalone `STATUS: PASS` or `STATUS: HOLD` line and a standalone `TESTS: <exact commands/results>` line. PASS denotes only scoped research validation and never production approval. If you cannot confidently establish both, write STATUS: HOLD.
