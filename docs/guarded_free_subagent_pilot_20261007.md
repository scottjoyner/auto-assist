# Guarded free-subagent pilot — 2026-10-07

## Scope and outcome
Fleet Commander executed one live, read-only OpenCode subagent through
`scripts/guarded_free_subagent.py` wrapping the existing exact-model
`scripts/launch_kilo_exact.py` finalizer. No paid fallback or automatic retries.
Pilot: `guarded-processor-audit-20261007`, requested exact
`kilo_free/stepfun/step-3.7-flash:free`.

- Exit 0; 15.01 seconds; 10,973 input / 805 output tokens; two reasoning
  steps and two read tools; 53.64 observed output tokens/second.
- No JSONL error events; recorded cost $0.00; verified finalized stdout
  SHA-256 and attributable completion manifest.
- Manifest: `/home/scott/git/.trace-harvest-20261007-guarded/state/manifests/ses_ee71a356bffe8OyNc8Odg7JyDG.json`.
- Raw trace: `/home/scott/git/.trace-harvest-20261007-guarded/traces/guarded-processor-audit-20261007.stdout.jsonl`.
- **Review-quality verdict: rejected.** The model alleged negative token
  counts reduce the budget, but the implementation ignores negative values.
  Trace success and task correctness must be separate indicators.

## Guard behavior
One cooperative x1-370 admission slot via nonblocking file lock; a persisted
minimum 180-second gap between completed calls. Per-run defaults:
60k cumulative input tokens, 4k output, three completed reasoning steps,
four tool calls, and 90 seconds. This pilot used a stricter 60 seconds and
2k output tokens. A real HTTP 429 or 503 event terminates the agent and
records one-hour cooldown; HTTP 401 or 402 records 24-hour cooldown.
A budget or time stop targets the OpenCode PID so the launcher can finalize
the ledger; no automatic escalation of concurrency, token caps, or providers.

Run through the guard only; direct `launch_kilo_exact.py` bypasses admission.
No central quota claim: the lock covers only callers sharing the same guard
root on the same host, not interactive OpenCode sessions, Hermes, AutoClaw,
other hosts, or upstream account-wide consumption. Obtain provider quota
limits and a centralized gateway before enabling two concurrent agents.

## Trace isolation and recovery debt
The Fleet Commander runtime has a separate OpenCode database at
`/home/scott/.local/share/mcp/dc-isolated/.home/.local/share/opencode/opencode.db`.
The account-level `/home/scott/.local/share/opencode/opencode.db` is a
different database. All three checked experiment session IDs appeared in
the isolated database and none in the account-level database. Export and
reconcile both **read-only**; do not substitute or merge them silently.

The older `highroi-router-security-20261007` record still says `running`
although launcher PID 3358228 and OpenCode PID 3358249 are absent.
Its JSONL includes 50 tools and approximately 2.65M reported input tokens
without a normal finish event. Preserve the original ledger and trace;
confirm a terminal DB state before attributing a successful outcome.
Do not replay that objective until budgets and reconciliation are verified.

## Next acceptance and growth gate
1. Use unique titles; verify output/stderr SHA-256 and manifest status, model,
   zero cost, bounded tools, tokens, duration, and clean exit before acceptance.
2. Score procedural quality from independently inspected code/tests, not
   the model's unsupported claims. Keep the original model response in trace.
3. Track 429/503/401/402, elapsed time, output tokens/second, input tokens,
   tool loops, provider attribution and time since last launch.
4. Require multiple consecutive clean, useful single-agent outcomes and
   observed provider quota headroom before reducing cooldown or adding slots.
5. Archive verified immutable raw traces to an authorized NAS path without
   deleting local copies until hashes and restore checks agree.
