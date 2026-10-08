# Trace-first execution adapter: synthetic acceptance and rollout hold

**Decision date:** 2026-10-07 (America/New_York)
**Decision purpose:** Build verifiable, local pre-execution trace custody before dispatching complex cross-node commands.
**Decision-maker:** Operator requested implementation; this branch is an engineering proposal, NOT production execution authorization.
**Decision:** Add a **disabled-by-default** synthetic-only trace-probe adapter to the existing AssistX node-agent claim path. Preserve AssistX/Neo4j as unique assignment and claim authority; do not add a second scheduler or give auto-router worker placement authority.
**Reopen when:** Independent per-node durable evidence, explicit node-bound authentication, restore rehearsal, and cross-node negative canaries all pass.

## Current scope

- Code: `src/assistx/trace_execution_adapter.py`, `src/assistx/fleet_node_agent.py`.
- Fixture runner: `scripts/trace_execution_canary.py` (NO real AssistX claim or network calls).
- Environment example: `config/trace_execution.example.env`. Disabled by default.
- Two built-in probes: `probe.noop.v1` and `probe.echo.v1`. Neither launches a process, makes a network request, or changes machine state.
- Generic shell and other existing execution types are **not** routed through this experimental adapter; do NOT present them as covered by its tracing.
- A real node-agent request can use task type `trace_probe` only when explicitly enabled and carrying the `trace-probe` capability, and only after a successful AssistX claim response containing the authoritative task, claim ID, and exact node target.

## Hypothesis, prediction, and method

**Hypothesis:** A node can reject a synthetic command before execution if the local durable journal is unavailable, and it can reject stale/duplicate claims without running twice.

**Prospective prediction for the next cross-node experiment:** No-op/echo canaries will append two valid SHA-256-linked, fd-synced receipts; wrong node, missing claim, dangerous command IDs, absent audit root, tampered journal, and failed pre-execution writes will produce a denial and zero executed probes. Concurrent same-claim requests should yield one success. A failed completion write must deny unverified success and leave a prepared-only record that blocks retries. This cross-node prediction was documented after initial local unit tests, so do **not** present it as a preregistered prediction for those already completed tests.

**Method:** pytest executes only synthetic in-process probes in temporary 0700 directories. The mock-node-agent integration checks the claimed task is authoritative and verifies a final complete POST payload, without invoking actual AssistX networking. The locally run CLI canary writes only these same no-op records.

**Measured observations (initial local run):** 22 unit/regression tests passed in 0.09 seconds for the adapter and existing shell/recovery controls. A subsequent broader run passed 34 tests in 0.19 seconds. The standalone local synthetic no-op canary wrote a prepared/completed pair and independent SHA-256 chain verification returned two records and no errors. A separate existing `test_execution_control.py` test fails to import `langgraph` on both this worktree and untouched main; it is not evidence of a new adapter regression. These observations are from an isolated local worktree; no fleet-wide installation or remote service changes were performed. No physical cross-node claim/lease or disk failure-injection drill has run.

## Journal contract

Required directory: pre-provisioned, local owner-private mode 0700, not a symlink; filesystem not a shared plaintext NAS mount. `journal.jsonl` is mode 0600 and rejects symlinks and hard links.

Each journal row contains schema, sequence, timestamp, node/task/claim IDs, command ID, input/output SHA-256 digests, event, preceding hash, and row hash. `prepared` must be synced to local disk before built-in probe code runs; `completed` follows after output digest calculation. File locks serialize concurrent writers. Verification requires intact JSON lines, continuous sequence/hash chain, and matching node identity for append. Output and message content are **not** saved in the journal.

SHA-256 hash chaining detects accidental corruption/unsanctioned modifications when the expected head is retained, but does not itself authenticate writers; separately anchor signed heads to AssistX and encrypted immutable backup before considering adversarial tampering covered.

If completion persistence fails after the probe runs, its outcome is **unknown/denied**, not reported as successful. A prepared-only attempt blocks further execution for the same task/claim ID. Manual audit reconciliation must handle those attempts.

## Reproduction (isolated; no live execution)

```bash
cd /home/scott/git/wt-assistx-trace-execution-20261007
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_execution_adapter.py \
  tests/test_fleet_node_shell_gate.py \
  tests/test_fleet_node_recovery.py
PYTHONPATH=src python3 -m compileall -q \
  src/assistx/trace_execution_adapter.py src/assistx/fleet_node_agent.py \
  scripts/trace_execution_canary.py
# Explicitly local, private, synthetic-only audit journal:
install -d -m 0700 /home/scott/.local/state/assistx/trace-execution-canary
PYTHONPATH=src python3 scripts/trace_execution_canary.py \
  --node-id x1-370 \
  --audit-root /home/scott/.local/state/assistx/trace-execution-canary
PYTHONPATH=src python3 scripts/trace_execution_canary.py \
  --node-id x1-370 \
  --audit-root /home/scott/.local/state/assistx/trace-execution-canary \
  --verify-only
```

The CLI produces a synthetic claim ID **only for local testing**. It is NOT authority to run physical fleet tasks.

## Hard acceptance gates before real commands

1. **Local provenance:** Repeat full baseline tests, verify pristine repository delta, audit-root ownership, journal chain, and no raw sensitive output.
2. **Durability:** Force disk/full-permission/open/fsync failures; prove deny-before-execution, unknown-on-incomplete, duplicate and tamper rejection. Measure write latency and journal growth.
3. **Identity/fencing:** Authenticate physical node with a node-bound credential, consume ONLY an authoritative current AssistX claim and signed projection/lease, and reject wrong-node, stale, expired, revoked, and replayed claims. Basic-auth-only claim requests are NOT enough for privileged execution.
4. **Archive custody:** Local raw receipts should be encrypted and backed up incrementally to the existing audited NAS custody path, with verified restore and an external signed head anchor; remove no history. Ensure no plaintext NAS leftovers.
5. **Remote node:** Install audited code in a separate user-owned shadow environment on xwing only AFTER local trace and remote trace producer are proven. No production service restarts, port opens, or auto-start until acceptance.
6. **Transport experiment:** Tailscale private path first; OpenTunnel remains an uninstalled optional transport. No access-path promotion or dual execution authority.
7. **Activation:** Explicit operator approval and a fenced, allowlisted typed real operation are mandatory. Leave `FLEET_UNSAFE_SHELL_TASKS_ENABLED=false`.

## Known limitations and honest status

- This is a tracing and executor **canary**, not a general fleet command runner. Existing LLM/recovery/benchmark execution has not been migrated behind this trace gate.
- There is no independent read-after-write proof from the target's filesystem during a network outage.
- Per-record rereading verifies the entire journal on every write (O(n) per append); benchmark and replace with a safe indexed checkpoint mechanism before high-volume rollout.
- Cross-node trust, live AssistX claim revocation, trace export, and retention are still future acceptance work.
- Existing Fleet Commander audit overlay is distinct: the x1-370 local archive verified 2,350 content references and backup archives were present in this evaluation; the remote archive had zero records. That absence is NOT evidence of remote production tracing.
- Keep this branch local or review-only until authorized deployment. Do not add a global `trace-probe` capability to every node without per-node storage and fencing proof.
