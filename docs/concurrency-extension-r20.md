# AssistX outbox claim concurrency — extension beyond N=8 (r20)

**Scope:** offline SQLite harness on one host (x1-370), 2026-11-27. The real
`OutboxClient._claim_next()` runs against a fresh file DB under the SSD scratch
`TMPDIR`; no live service, no HTTP delivery, no live DB, no production change.
See `tests/test_concurrent_claims_extension_r20.py` and the R19 report for the
harness contract. These are **observations for this host, workload and time
window — not a universal safe-claimant number**.

## Measured envelope (5 focused runs)

Seeded rows: N×4 claimable, split pending/failed, plus two oldest quarantine
holds (R4 marker). One `OutboxClient` per thread, released together on a
barrier; 15 s total wall budget per case; 2 s SQLite connect busy timeout.

| Case | Rows claimed once | Busy errors | Other errors | Wall time | Per-call latency (mean / max) |
|---|---|---|---|---|---|
| N=16 threads, 64 claimable | 64/64, 64 distinct IDs and owner tokens | 2–14 per run | 0 | 2.06–2.96 s | 345–424 ms / 2.00–2.04 s |
| N=32 threads, 128 claimable | 128/128, 128 distinct IDs and owner tokens | 30–31 per run | 0 | 3.05–4.88 s | 406–434 ms / 2.00–2.01 s |
| Held writer lock, 16 threads, 0.05 s busy timeout | 0 claimed | 16/16 `database is locked` | 0 | ~0.05 s | ~51–52 ms mean |

N=32 was **not skipped**: every run finished well inside the 15 s budget.
The harness skips it explicitly (with a printed reason) only if a future run
has a still-live worker or exceeds that budget. Safety assertions — no double
claim, no owner-token reuse, no marker claimed, unchanged marker rows and an
intact `LIKE 'quarantine:%'` guard — run before any skip, so a genuine race
would be reported as a failure, never hidden by a timeout.

**Safety result:** no double claim, no token reuse and no quarantine-guard
breach at either N=16 or N=32. Fencing still comes from SQLite's `BEGIN
IMMEDIATE` write reservation plus the re-asserted guarded `UPDATE`; each
client's process-local `threading.Lock` is not in this path.

## Failure mode under higher contention

`_claim_next()` opens a fresh connection with `timeout=10` in production and
wraps SELECT + guarded UPDATE in `BEGIN IMMEDIATE`. Claims are serialized by a
single SQLite write reservation; every other claimant waits on the busy
handler. In this harness the timeout was shortened to 2 s so a stuck writer
fails fast.

At N≥16 that wait is visibly hit: individual calls raise
`sqlite3.OperationalError: database is locked` after ~2 s (9/16 and 31/32
claimants in the first run; 2–14 and 30–31 across repeats). The queue still
drained in these runs only because redundant claimants kept calling — a
caller that treats the exception as fatal stops claiming. With a held
`BEGIN IMMEDIATE` RESERVED lock and a 0.05 s timeout, all 16 claimants failed
busy and **no** row was claimed; the lock was surfaced, not swallowed or
returned as `None`. `_claim_next()` neither retries nor backoffs on busy.

The timeout is load-sensitive, not a clean N threshold: in one combined-suite
run the pre-existing R19 N=8 case also failed on `database is locked` at its
2 s timeout (1 failed / 61 passed). Two subsequent suite runs — 59/59 without
this module and 62/62 with it — passed, and R19 passes 4/4 in isolation. This
is consistent with a transient busy failure, not a fencing race.

So the envelope here is a *fencing* envelope (correctness held), not a
*liveness/throughput* guarantee: beyond N=8 a meaningful fraction of callers
see `database is locked` under this workload.

## What a universal limit would require

1. **Caller-side retry policy** — bounded retry with jitter/backoff for
   `OperationalError: database is locked` / `busy`, with a deadline and an
   explicit give-up signal, instead of an unhandled exception.
2. **A measured `busy_timeout` / WAL decision** for the real DB: raise the
   timeout, enable WAL so readers do not block the writer, or both — chosen
   from sustained-throughput and p99-latency data, not from this sub-ms
   claim-only harness.
3. **Realistic workload measurement** — HTTP delivery/ack, lease durations,
   flusher loop and backlog churn, with success/busy/error counts and latency
   under those conditions.
4. **Cross-host and network-filesystem checks** — multiple machines and the
   actual storage backend (SQLite file locking over NFS/network mounts differs
   from local Linux).
5. **Crash / SIGKILL lease-recovery and concurrent legacy-schema migration
   tests** — a claimant dying mid-transaction, and several clients starting
   against an older schema at once.

No production code was changed; no larger claimant count is certified by this
slice.
