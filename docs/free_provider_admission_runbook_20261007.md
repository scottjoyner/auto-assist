# Free-provider admission and trace runbook — October 7, 2026

## Current deployment
- x1-370 user timer: `fleet-provider-presence.timer` every 15 minutes.
- Local catalogs refresh at minimum every 15 minutes; public provider metadata
  at minimum every 30 minutes, with independent 429/503 and auth cooldowns.
- Installed executable: `~/.local/lib/fleet/provider_presence_watch.py`.
- Private state: `~/.local/state/auto-assist/provider-presence.json`.
- Reapply a code update with `bash scripts/install_provider_presence_timer.sh`.

## Two distinct gates before a guarded Kilo agent
1. Verify the private x1-370 provider-state file is owned by the account,
   mode 0600, has a Kilo catalog_reachable status, and was checked in the last
   hour. This is a necessary metadata signal, not inference proof.
2. Query **both** account and Fleet Commander isolated OpenCode databases
   read-only. Deduplicate by session ID. Block if either DB is absent, query
   fails, 2000 recent records would be exceeded, recent Kilo spend is nonzero,
   there are at least three recent Kilo sessions, or observed session-lifetime
   usage recently updated within 15 minutes reaches 60k input/6k output tokens.

The second gate deliberately counts an entire session's lifetime tokens if
that session was updated within 15 minutes. Consequently it may overestimate
real short-window usage; **it must not be reported as upstream token usage per
minute or an authoritative quota**. It also cannot see uncommitted streaming
usage or clients that do not write to either local OpenCode database.

Each denied provider/budget preflight writes a private `.admission.json`
decision without starting inference. Allowed launches still pass through the
existing one-slot local lock, 180-second cooldown, bounded token and tool
limits, exact-model attribution, and trace finalizer. Free generation should
remain opt-in and read-only until quality acceptance is independently proven.

## Verification
- Tests: `python3 -m pytest -q tests/test_guarded_free_subagent.py tests/test_provider_presence_watch.py tests/test_snapshot_free_usage.py tests/test_launch_kilo_exact.py tests/test_summarize_free_trace_manifests.py`.
- Monitor: `python3 scripts/provider_presence_watch.py` (GET/CLI metadata only).
- Usage: `python3 scripts/snapshot_free_usage.py --days 1` (read-only;
  output contains local session titles and identifiers; do not publish).
- Service: `systemctl --user show fleet-provider-presence.service -p Result
  -p ExecMainStatus` and `systemctl --user list-timers --all`.

## Not closed / next infrastructure slice
The admission lock and budget gate are local to x1-370; they do not atomically
reserve capacity across Hermes, AutoClaw, OmniRoute, other machines, or API
accounts. Before increasing concurrency, implement one centralized per-provider
admission authority with explicit short leases, request/token counters,
idempotent release, a retry-after circuit breaker, and immutable decision logs.
Use the auto-router API only behind an explicitly reviewed authority contract:
do not change live provider routing or permission boundaries as a side effect
of observational work.

OmniRoute dashboard was unreachable on 127.0.0.1:20128, and other tested
local ports did not expose the expected health route. Direct Cohere/xAI
credentials were not available to the monitoring process; never treat consumer
free chat as automatically authorizing cost-free developer API tokens.
Preserve the previous security-review orphan ledger as incomplete, and keep
its original raw stdout JSONL, immutable manifest evidence, and DB session.
