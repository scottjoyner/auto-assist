# Physical trace-read admission: durable local ledger research (2026-10-09)

**Status: RESEARCH / NO-GO FOR PRODUCTION.** Stacked on draft [#166](https://github.com/scottjoyner/auto-assist/pull/166), which already proves a nonexpiring Redis hash cannot survive missing Redis state. No runtime/API route imports this prototype. It does not exercise or modify production Redis, Neo4j, AssistX, NAS, Tailscale, or providers.

## Hypothesis and deliberately narrow claim

A local, nonexpiring SQLite ledger on a single stable POSIX filesystem can serialize concurrent *local* process reservations without Redis and preserve conservative occupancy after a worker crash. It cannot provide cross-host hard admission, protection from filesystem snapshots/rollback, or actual Neo4j termination evidence.

The previous Redis quarantine admitted 2 physically active reads with cap=1 after Redis state was reset. This branch tests an independent local durable ledger that denies a replacement when its reservation survives. In addition it contains two explicit falsification experiments showing why that **still isn't sufficient**:

- Two independent copies of an empty local ledger each admit one read (aggregate two physically active despite cap=1). Per-host SQLite is **not** a distributed consensus source.
- A verifier who signs an untrue "remote query terminated" receipt can release a token while the query is still active. Ed25519 authenticates the statement, **not the fact**. Real independent termination evidence and independently controlled key custody are required.

## Contract / guardrails

- Offline helper `bootstrap_disposable_fixture` only creates a fresh `/tmp/assistx-trace-ledger-test-*/trace-ledger-test.sqlite`, no overwrite/rearm. **Never call this helper from API startup.** Runtime constructor requires a preexisting SQLite file, externally pinned UUIDv4 epoch and 32-byte verifier public key.
- `sqlite3` uses URI `mode=rw` (no creation), `BEGIN IMMEDIATE` for cross-process serialization, `PRAGMA synchronous=FULL`, and a maximum 16 configured slots. The caller must use a **single local** POSIX file, never network-mounted `/nas`/NFS/CIFS.
- Reservation is nonexpiring. Query references are unique and bounded. Crashed or timeout-uncertain workers keep capacity occupied; do not auto-release, TTL-reap, or trust HTTP/driver exceptions as closure evidence.
- Every query admission checks the ledger epoch, schema and inode identity. Missing/renamed/replaced file fails closed for the existing guard. This does **not** detect offline rollback on the next process startup if an independently pinned epoch is reused.
- Release requires a matching token, query reference, epoch, exact statement shape and independently signed Ed25519 research closure receipt. Wrong, changed, replayed, or unsigned receipts cannot release a slot. The caller is **not** authorized to mint receipts.
- Any SQLite lock, integrity/identity failure or corrupted/unavailable database denies the request; no caller proceeds to Neo4j under an admission error. No secrets, private signing keys, or production identities are stored.

## Executed acceptance on x1-370

Isolated Git worktree: `/home/scott/git/.worktrees/assistx-ledger-research-20261009`

```sh
python3 -m pytest -q tests/test_trace_durable_ledger_research.py
```

**18 passed, zero warnings** on native Python 3.12 after converting child workers to spawn. Tests cover local thread/process concurrency, prerecorded 1/3/5/10 contender counts, crash without reclamation, durable reopening, missing ledger without auto-creation, inode replacement, wrong epoch, unconfigured bootstrap refusal, forged/changed/replayed Ed25519 receipts, and the two **passing expected-negative controls** for split-brain copies and signed-false termination claims. Every filesystem write was to a disposable directory under `/tmp` and cleaned by test fixtures. No API/Redis/graph request.

## Non-acceptance / gating experiments

This is **not** a substitute for a durable distributed physical-work authority. Before considering admission wiring for `/api/traces`, `/timeline`, or `/payload-preview`:

1. Decide a single authoritative distributed epoch/ownership store with crash-safe recovery and a **separate durable witness**. Prove split-brain, Redis state loss, persistent-store rollback, reconnection and version/epoch swaps do not admit a successor while any old query may still exist. If ambiguous, deny all.
2. Give each actual Neo4j transaction a uniquely recorded server-owned ID; obtain independent observation of remote completion or cancellation, not just client timeout or callback assertion. Bind the signed receipt to epoch, token, server-side transaction identifier and evidence, and isolate the signing key away from API workers.
3. In a disposable graph + multiple authenticated API workers, preregister 1/3/5/10 simultaneous clients, long-running read, server/driver crash and network interruption. Observe server transactions during and after cancellation, Redis failover/restart, hard capacity, time budgets and aggregate DB hits. A mere local SQLite cap pass cannot close [#148](https://github.com/scottjoyner/auto-assist/issues/148).
4. Independently verify all trusted proxy ingress paths and header stripping under [#149](https://github.com/scottjoyner/auto-assist/issues/149) and authenticated operator/payload scope. Resolve existing full-repo red CI and get operator approval on rollout/rollback.
5. **No production integration or flag activation.** These scripts do not grant physical query authority or deletion/retention authority. Preserve the existing readonly trace client draft #179 and its separate exact-head synthetic acceptance.

### Release disposition

**KEEP #148 OPEN.** This experiment materially eliminates one failure class only *within a shared surviving local ledger*, and positively reproduces the two unsolved failures (split-brain and false closure evidence). Draft only; never advertise as global hard fence.
