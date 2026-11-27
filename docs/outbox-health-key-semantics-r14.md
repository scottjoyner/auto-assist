# Outbox health key semantics (R14, 2026-11-27)

This document defines **every key returned by `read_queue_health()`** at revision
`a5aa5574f110233178948c10543eac5dac6b2a0a` (the pushed R13 tip, plus the R14
documentation/tests only). It is derived by reading the code at its exact path:

- `src/assistx/outbox_client.py::read_queue_health()` — lines 106–222.
- Supporting constants in the same module: `STATUSES` (line 34),
  `STALE_READINESS_S` (line 28), `_QUARANTINE_CLAIM_GUARD` (line 32),
  `_parse_iso()` (lines 94–103).
- The thin wrapper `OutboxClient.queue_health()` (lines 623–632) calls the same
  function with `read_only=False`; it does **not** change the key set.

No key is listed here unless the code actually produces it. Where the request
for this document names keys that the code does not return, that is called out
explicitly in "Keys that are NOT returned" below rather than invented.

## Scope and invariants of the probe

`read_queue_health(db_path=DEFAULT_DB_PATH, *, stale_after_s=None, now=None,
read_only=True)`:

- Opens the DB read-only (`file:<path>?mode=ro`, `uri=True`, `timeout=5`) unless
  `read_only=False`. It never creates, migrates, purges or writes to the outbox.
- A missing path returns the initial dict immediately with `status="absent"`.
- A `sqlite3.Error` from connect or query returns the initial dict with
  `status="error"` and a truncated `reason`; `ok` stays `True`.
- All counts come from the **same** connection (status buckets, oldest row,
  quarantine count, claimable-pending count) so they describe one snapshot.

## Complete key inventory

The base dict is created at lines 125–145 with these **15 keys**, always present
on every return path except `reason` (popped on the healthy `ok` path):

| Key | Type | Exact derivation | Legacy-schema fallback (no `claim_owner`) |
|---|---|---|---|
| `db_path` | `str` | `os.path.expanduser(db_path or DEFAULT_DB_PATH)`. No SQL. | Same. |
| `status` | `str` | State machine, not SQL: `"absent"` (path missing), `"error"` (outer `sqlite3.Error`), `"stale"` (oldest age > threshold), `"ok"` (healthy). Set at lines 127, 189, 212, 219. | Same; a legacy schema is not an error. |
| `ok` | `bool` | `True` initially; set `False` **only** on the stale branch (line 213). Unreadable queue deliberately keeps `ok=True` (line 190). | Same. |
| `reason` | `str` (or **absent**) | `"outbox database not present"` (absent); `"outbox unreadable: " + str(exc)[:200]` (error); `"oldest pending event is Xh old (threshold Yh)"` (stale). Popped entirely via `health.pop("reason", None)` when `status="ok"` (line 221). | Same; a missing `claim_owner` column does not add a `reason`. |
| `stale_after_s` | `int` | `STALE_READINESS_S` when `stale_after_s is None`, else `int(stale_after_s)`. `STALE_READINESS_S = int(os.environ.get("ASSISTX_OUTBOX_STALE_S", "86400"))`. No SQL. | Same. |
| `pending` | `int` | From `SELECT status, COUNT(*) FROM outbox GROUP BY status`; the loop `health[status] = int(count)` writes the row whose `status == "pending"`. Initialized `0`, so present even when no such row exists. | Same query, independent of `claim_owner`. |
| `failed` | `int` | Same `GROUP BY` query, row with `status == "failed"`; initialized `0`. | Same. |
| `claimed` | `int` | Same `GROUP BY` query, row with `status == "claimed"`; initialized `0`. | Same. |
| `delivered` | `int` | Same `GROUP BY` query, row with `status == "delivered"`; initialized `0`. | Same. |
| `dead` | `int` | Same `GROUP BY` query, row with `status == "dead"`; initialized `0`. | Same. |
| `quarantined` | `int` | `SELECT count(*) FROM outbox WHERE claim_owner LIKE 'quarantine:%'` (lines 164–166). Counts **all** statuses carrying an operator hold marker; independent of the status buckets and of `total`. On `sqlite3.Error` the local is set to `None`, then `int(quarantined[0]) if quarantined else 0` (line 199) → `0`. | `0`: a legacy table has no `claim_owner`, so no hold marker can exist. No error, no `reason`. |
| `claimable_pending` | `int` | `SELECT count(*) FROM outbox WHERE status='pending' AND (claim_owner IS NULL OR claim_owner NOT LIKE 'quarantine:%')` (lines 175–178), where the parenthesised guard is the module constant `_QUARANTINE_CLAIM_GUARD`, the same guard used by the claim path. On `sqlite3.Error` the local is set to `None`. | Falls back to `int(health["pending"])` (lines 202–204): with no `claim_owner` column, every pending row is claimable. No error, no `reason`. |
| `total` | `int` | Initialized `0`; the `GROUP BY` loop adds `int(count)` for **every** returned status row: `health["total"] += int(count)` (line 198). Since `GROUP BY status` partitions the table, `total` is the row count of `outbox` (it also includes any out-of-vocabulary status group — see caveat). | Same. |
| `oldest_pending_at` | `str` or `None` | `SELECT MIN(created_at) FROM outbox WHERE status IN ('pending', 'failed', 'claimed')` (lines 157–160); value is `oldest[0]` (an ISO timestamp string) or `None`. Note "delivered"/"dead" rows are excluded. | Same query/columns; legacy tables have `created_at`, so no fallback is needed. |
| `oldest_pending_age_s` | `float` or `None` | Only when `_parse_iso(oldest_pending_at)` succeeds: `round(max(0.0, (now - oldest_dt).total_seconds()), 3)` (lines 207–210). `now` is the caller's `now` or `datetime.now(timezone.utc)`. Otherwise the initial `None` is retained. Negative ages clamp to `0.0`. | Same. |

### Shape by path (`status` / `ok` / `reason`)

- **Absent** (`os.path.exists(path)` false): all 15 keys, `status="absent"`,
  `ok=True`, `reason="outbox database not present"`, all counts `0`,
  `oldest_pending_at=None`, `oldest_pending_age_s=None`.
- **Error** (outer `sqlite3.Error`): all 15 keys, `status="error"`, `ok=True`,
  `reason="outbox unreadable: …"`, all counts remain `0`, age fields `None`.
  The error return happens before the `GROUP BY` loop, so bucket values are the
  initialized zeros, not partial data.
- **Healthy** (`ok`): `14` keys — `reason` is removed; `status="ok"`, `ok=True`,
  counts and age fields populated.
- **Stale** (age > `stale_after_s`): all 15 keys, `status="stale"`, `ok=False`,
  `reason` describes the age and threshold; counts are populated. The function
  returns early at line 218, after all counts/age fields have been set.

### Legacy-schema fallback, summarised

A legacy `outbox` table is one without the `claim_owner`/`claimed_at` columns
added after the original schema. The two marker-dependent queries each raise
`sqlite3.Error`, which is caught locally (lines 167–170 and 179–183) so the
probe stays non-fatal:

- `quarantined == 0`
- `claimable_pending == pending`
- `status == "ok"`, `ok is True`, `reason` absent
- every other key derives exactly as on a current schema.

The status buckets, `total`, `db_path`, `stale_after_s` and the age fields never
depend on `claim_owner` and therefore need no fallback.

## The `claimable_pending + quarantined == pending` identity

This equality is **conditional**, not universal:

```
claimable_pending = count(pending AND NOT quarantine-marked)
quarantined       = count(any status AND quarantine-marked)
=> claimable_pending + quarantined
   = pending + count(quarantine-marked AND status != 'pending')
```

It holds exactly when every `quarantine:%`-marked row also has
`status='pending'` — the operator-hold convention (holds keep the original
status). The R14 fixture is built that way, so it asserts the identity. A
quarantine marker on, say, a `failed` or `claimed` row would make the left side
exceed `pending`; the documented counts remain correct because `quarantined` is
deliberately status-independent.

## Keys that are NOT returned

The request text mentions `retry` and `attempt_buckets`. **Neither key exists in
`read_queue_health()` at this revision** — the code was read by exact path and
no such assignment appears anywhere in lines 106–222. Consumers must not index
`health["retry"]` or `health["attempt_buckets"]`. `attempt_count` exists as an
`OutboxEntry` field and an `outbox` column, but this probe never aggregates it.
`OutboxClient.get_stats()` (lines 634–652) returns only `total` plus the five
`STATUSES` — it is a different shape and also has no retry/attempt buckets.

## Caveat: emergent keys from out-of-vocabulary statuses

The bucket loop writes `health[status]` for **every** status string returned by
`GROUP BY`, not just the five in `STATUSES`. Therefore a row whose `status` is
outside `("pending", "failed", "claimed", "delivered", "dead")` would add an
extra key (and contribute to `total`). That is emergent behaviour, not a
documented bucket: the contract above covers the five initialized status keys.
The read-only invariant and the identity in the previous section are unaffected.

## Validation reference

`tests/test_outbox_health_key_semantics_r14.py` exercises the full 14-key
healthy set (identical for current and legacy schemas, since both are `ok` and
pop `reason`), the `claimable_pending + quarantined == pending` identity on a
mixed in-memory fixture, and the legacy-schema fallback (`quarantined == 0`,
`claimable_pending == pending`, `reason` absent). See
`round14-session4-assistx-20261126.md` for commands and results.
