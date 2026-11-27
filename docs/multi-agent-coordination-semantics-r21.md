# Multi-agent coordination semantics via the AssistX outbox (r21)

**Documentation only.** No production code, schema, service, compose or live DB was changed.
Base: pushed `agent/concurrency-extension-r20-20261126` @ `fbec101`. All statements below were
checked against `src/assistx/outbox_client.py` on that base (the read-only source assertion in
`tests/test_multi_agent_coordination_semantics_r21.py` fails the test if the code drifts) and
against the R19/R20 measured envelopes. These are offline, single-host observations — not a
universal claimant limit, not a production SLO, and not evidence about a live queue.

## 1. The outbox is the coordination plane, not an in-process lock

Multiple opencode agents do not share a `threading.Lock`. The shared coordination store is the
SQLite outbox file (`DEFAULT_DB_PATH = ~/.assistx_outbox.db`, table `outbox`). The claim protocol
in `OutboxClient._claim_next()` is the fence that decides which agent owns which unit of work.
Each agent is a separate `OutboxClient` (and typically a separate process); whatever reopens the
same `db_path` sees the same claims. The client's `self._lock` guards only process-local
non-claim writes — it cannot serialize agents and is deliberately not part of the claim path.

## 2. Claim-once semantics (as implemented)

Row states are `pending`, `failed`, `claimed`, `delivered`, `dead` (`STATUSES`). A row becomes
`claimed` before delivery and is handed back by `_release()` to a terminal/retry status.

1. `_claim_next()` opens a **fresh** connection (`timeout=10` in production), sets
   `isolation_level = None`, and runs `BEGIN IMMEDIATE` — one SQLite write reservation covers
   selection and marking.
2. It selects the **oldest** deliverable row: `status IN ('pending','failed')` **or**
   `status='claimed'` with an expired lease (`claimed_at IS NULL OR claimed_at < stale_cutoff`).
   An optional `exclude` list prevents one `flush()` call from retrying rows it already tried.
   The operator quarantine guard is applied here.
3. It runs a **guarded UPDATE**: `SET status='claimed', claim_owner=<uuid4().hex>,
   claimed_at=<now>` with a `WHERE` clause that *re-asserts* deliverability **and** the
   quarantine guard. `rowcount == 0` triggers `ROLLBACK` and returns `None`.
4. On success it `COMMIT`s and returns an `OutboxEntry` carrying the token in `claim_owner`;
   every later write must present that token. On any other exception it `ROLLBACK`s and re-raises.
5. `_release()` updates only `WHERE outbox_id=? AND status='claimed' AND claim_owner=?`, clears
   the claim columns, and returns `False` if the caller no longer holds the claim. A caller whose
   lease was recovered **must not** overwrite the row or count a delivery as its own.

Two consequences agents must internalize:

- **`None` is not an error and not proof of an empty queue.** It means either "no deliverable row
  found" or "lost the guarded UPDATE race to another claimant." Treat it as *no claim obtained
  this tick* and move on; do not deliver anything and do not mark the row failed.
- **Delivery is at-least-once, never concurrently at-least-once.** A claimant killed mid-delivery
  loses the row only after the lease expires, at which point another agent may deliver it again.
  Exactly-once behavior must come from the destination (e.g. `event_id` idempotency), not from
  the outbox. `enqueue()` already deduplicates by `event_id` under `BEGIN IMMEDIATE`.

`read_queue_health()` is a read-only observer: it opens `mode=ro`, never creates/migrates/purges,
and reports `quarantined` and `claimable_pending` as additive keys independent of status buckets.

## 3. Quarantine markers are holds, not claims

Operator quarantine is encoded as a `claim_owner` label matching `LIKE 'quarantine:%'`; the row
keeps its original `status` (there is no `quarantined` status). The guard
`(claim_owner IS NULL OR claim_owner NOT LIKE 'quarantine:%')` is applied to **both** the claim
SELECT and its guarded UPDATE, so a marked row cannot be claimed even if it is the oldest
deliverable row, and a row marked between SELECT and UPDATE cannot be claimed either.

- **Lease expiry does not override quarantine.** A quarantined row whose `claimed_at` is long
  expired stays put: the guard is independent of the lease predicate.
- Health counts markers separately from status buckets, so an agent must not infer claimability
  from `pending` alone — use `claimable_pending` or the claim result.
- Quarantine labels are operator-owned. Agents must not synthesise or clear them from the claim
  path; only `_release()` (token-fenced) clears `claim_owner`, and it never targets a marker row.

## 4. Lease expiry and recovery

`CLAIM_LEASE_S` defaults to **90 s** (env `ASSISTX_OUTBOX_CLAIM_LEASE_S`), chosen to exceed the
**15 s** HTTP delivery timeout while returning a crashed claimant's rows reasonably quickly.
`FLUSH_BATCH` defaults to 50 rows per `flush()` call. The stale cutoff is
`now - max(1, lease)`; a claimed row with `claimed_at` at or before that cutoff becomes
deliverable again.

- Recovery is **passive**: there is no heartbeat, lease extension or explicit handoff. A live but
  slow flusher is protected only by the lease being longer than its delivery timeout.
- Recovery assigns a **new token**, so the old owner's `_release()` returns `False`. The old owner
  must honor that `False` and stop treating the row as its own.
- Recovery is eventual, not instantaneous: it happens on the next claim pass after the lease
  lapses. No agent should assume a timed-out row is immediately available.
- **Cross-host caveat (untested):** `claimed_at` values are ISO UTC timestamps generated by each
  agent with its own `datetime.now(timezone.utc)`. Clock skew between hosts can shorten or
  lengthen effective leases. Keep hosts NTP-synced; this was not measured here.

## 5. Measured envelope and the liveness caveat

R19/R20 drove the real `_claim_next()` against fresh file-backed SQLite DBs under the SSD scratch
`TMPDIR`, with one `OutboxClient` per thread released on a barrier:

| Case (x1-370, host-specific) | Result |
|---|---|
| N=16, 64 claimable rows | 64/64 claimed exactly once, distinct IDs and tokens, markers untouched |
| N=32, 128 claimable rows | 128/128 claimed exactly once, distinct IDs and tokens, markers untouched |
| N=16 against a held `BEGIN IMMEDIATE` lock, 0.05 s busy timeout | 16/16 raised `database is locked`, 0 rows claimed |

Across five runs, 2–14 (N=16) and 30–31 (N=32) callers per run hit `database is locked` at the
harness's 2 s busy timeout, with zero other errors. The queue still drained only because
redundant claimants kept calling; a caller that treats the exception as fatal stops claiming.
The pre-existing R19 N=8 case occasionally hit the same busy error in a combined suite, so this
is load-sensitive, **not** a clean N threshold.

**Interpretation:** the N≤32 result is a *fencing* envelope (no double claim, no token reuse, no
quarantine breach) for this host, workload and time window. It is **not** a liveness/throughput
guarantee and not a certified safe claimant count. The failure mode is structural: `BEGIN
IMMEDIATE` serializes all claims, `_claim_next()` has no retry or backoff on busy, and the SQLite
busy handler raises `OperationalError: database is locked` at the connect timeout.

The R21 in-memory fixture in this branch is a smaller **invariant check**, not a capacity claim:
four real `OutboxClient`s sharing a `mode=memory&cache=shared` DB claimed 5/5 claimable rows
exactly once (4 fresh + 1 recovered expired lease), left the quarantine hold untouched, and saw
3 busy errors absorbed by the caller-side retry below. It is in-process and says nothing about
cross-process or live-DB behavior.

## 6. Recommended caller-side retry/backoff policy (not implemented here)

`_claim_next()` neither retries nor backoffs on busy. Its module contains no `OperationalError`
handling at all. Production **does** have an exponential *delivery* backoff
(`retry_base_s * 2**attempt`, capped at `retry_max_s`, driven by `attempt_count` and
`last_attempt_at`), but that is a different path and must not be confused with busy retry.
Until a production decision is made, agents should wrap calls themselves:

1. **Retry only** `sqlite3.OperationalError` whose message indicates busy/locked (`database is
   locked`, `database table is locked`). Re-raise every other error — schema, integrity, I/O.
2. **Bound both attempts and time:** e.g. at most 3–5 retries and a wall deadline of a few seconds
   for a claim call. On exhaustion, treat it as *no claim this tick*; do not deliver, do not mark
   the row `failed`, and let the next flusher tick or another agent try.
3. **Exponential backoff with full jitter:** e.g. `min(cap, base * 2**attempt)` with a random
   multiplier in `[0.5, 1.5)`. Jitter prevents synchronized lockstep retries that amplify the
   single-writer bottleneck. The R21 fixture uses `base=2 ms`, `cap=50 ms`, ≤6 attempts.
4. **Never convert busy into `None`-as-success, and never deliver without a claim token.** `None`
   means "no claim"; a busy exception means "try later." Both must leave the row untouched.
5. **Do not retry on `None`.** An empty claim is a normal outcome, not transient failure.
6. **Keep the retry envelope small relative to the lease.** Retrying longer than `CLAIM_LEASE_S`
   can hand a claim to another agent while this one still believes it owns it. Honor a `False`
   from `_release()` as the final authority.
7. **Treat this as load management, not a capacity upgrade.** The measured N≥16 busy fraction
   argues for a modest number of concurrent claimants plus jitter; raising `busy_timeout` and
   choosing WAL are separate, measurement-driven decisions (see R20's open items), not something
   a caller-side retry can substitute for.

No production code was changed to add this policy. Before adopting it broadly, validate
sustained throughput with HTTP delivery/ack and flusher churn, cross-host and network-filesystem
locking, and crash/SIGKILL lease recovery.

## 7. SensorGraph MCP envelope is a separate store

The AssistX outbox and the SensorGraph graph are **not** one coordination system:

| | AssistX outbox | SensorGraph graph |
|---|---|---|
| Store | `~/.assistx_outbox.db`, table `outbox` | separate SQLite DB (e.g. `fleet-traces.db`, WAL), graph tables + `coord_effects` |
| Fence | claim token in `claim_owner` + 90 s lease + guarded UPDATE | `BEGIN IMMEDIATE` write reservation; effects recorded as rows |
| Measured shape | N≤32 claimants, no double claim; busy from N≥16 | 32 `mode=ro` readers all succeeded; W=1 clean, W≥2 BUSY backpressure (~1–2 effective committers) |
| Caveat | host/workload-specific fencing, not liveness | synthetic fixture (30 ms hold, 75 ms `busy_timeout`), R=32 was the top of the grid, no saturation seen, R>32 unmeasured |

The graph numbers are an offline SQLite/WAL envelope from a synthetic fixture, **not** an MCP
session capacity: there is no MCP lease/claim/lock surface today (R23/R24 "test of absence"), and
`N_READERS`/`N_WRITERS` are parameters rather than a published `max_sessions` or SLO.

**Do not assume cross-store atomicity or shared fencing.** An outbox claim reserves no graph
state; a graph write reservation does not fence an outbox claim. They share no transaction, no
lock and no token. An agent that does both must treat them as independent failure domains and
apply each store's own retry/fencing rules; a busy error on one does not imply anything about the
other, and neither envelope bounds the other.

## 8. Operating checklist for multiple agents

1. Give each agent its own `OutboxClient` / connection; never share a claim token between agents.
2. Let `_claim_next()` choose work; do not pre-select rows with a separate read and then deliver.
3. Ignore `quarantine:*` rows entirely, including expired ones; never clear those labels.
4. Deliver only after a successful claim, and release with the token; honor `_release() == False`.
5. Wrap claim calls in the bounded, jittered busy retry of §6; re-raise non-busy errors.
6. Keep delivery well inside the 90 s lease; assume at-least-once and make the destination
   idempotent by `event_id`.
7. Coordinate graph work separately through its own store; do not extrapolate the outbox N≤32
   fencing result to graph sessions, or the graph 32-reader envelope to outbox claimants.

## 9. Provenance / verification

- Code read: `src/assistx/outbox_client.py` (`_claim_next`, `_release`, `read_queue_health`,
  `flush`, `_QUARANTINE_CLAIM_GUARD`, `CLAIM_LEASE_S`).
- Measurements: `round19-session4-assistx-20261126.md`, `round20-session5-assistx-20261126.md`
  and `docs/concurrency-extension-r20.md`; SensorGraph: `round20-session3-sensorgraph-concurrency-
  20261126.md` and `round23-session3-sensorgraphdb-20261126.md`.
- Offline checks in this branch: `tests/test_multi_agent_coordination_semantics_r21.py` —
  read-only source assertion + in-memory N=4 no-double-claim fixture; 2/2 passed (5 repeats),
  `ruff check`, `ruff format --check`, `py_compile` clean. SSD scratch caches only; no live DB,
  no push.
