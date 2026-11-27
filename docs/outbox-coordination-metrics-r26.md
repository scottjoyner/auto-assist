# Outbox coordination metrics (R26, 2026-11-27)

**Read-only specification — no production change, no live DB read or write, no
service/compose change.** This document defines the coordination metrics the
AssistX outbox can report **today**, derived by reading the code at its exact
path. It is the companion to `docs/outbox-health-key-semantics-r14.md` (which
pins the full `read_queue_health()` key set) and `docs/mcp-outbox-bridge-r22.md`
(which pins the three-plane / separate-store boundary).

Base: pushed `agent/claim-owner-validation-r25-20261126` @ `acd14147`.
Branch: `agent/outbox-coordination-metrics-r26-20261126` (local, not pushed).
Every key below is produced by one of the two read-only outbox surfaces in this
repo. No field is invented: where the request names a key the code does not
produce, that is called out explicitly in "Keys that do NOT exist".

## 1. The two read-only surfaces

The outbox has exactly two read-only reporting surfaces. They are different
calls with different key sets and must not be conflated.

| Surface | Exact path | Opens DB as | Emits |
|---|---|---|---|
| Health probe | `src/assistx/outbox_client.py::read_queue_health()` (lines 133–249); thin wrapper `OutboxClient.queue_health()` (lines 660–669, `read_only=False`, same key set) | `file:<path>?mode=ro`, `uri=True`, `timeout=5` (line 177) when `read_only=True` | One `dict`: status buckets, quarantine/claimable counts, staleness |
| Read-only audit CLI | `tools/audit_outbox_ro.py::_audit()` (lines 124–251) and `_result()` (lines 93–116); `--json` calls `_emit_json()` (lines 119–121) | `resolved.as_uri() + "?mode=ro"` + `PRAGMA query_only=ON` (lines 126–130) | One key-sorted JSON object (`--json`) or human text |

Neither surface writes. The health probe returns early on a missing path
(`status="absent"`, all counts `0`, lines 173–174) and on an outer
`sqlite3.Error` (`status="error"`, `ok` stays `True`, lines 213–221). The audit
CLI never issues INSERT/UPDATE/DELETE/DDL and never reads `payload_json`
(module docstring, lines 1–25).

## 2. Metrics the outbox CAN report (grounded)

### 2.1 From the health probe — `read_queue_health()`

All counts come from the **same** connection, so they describe one snapshot
(`src/assistx/outbox_client.py`, lines 181–212).

| Metric key | Exact source in `src/assistx/outbox_client.py` | Meaning |
|---|---|---|
| `pending` | `SELECT status, COUNT(*) FROM outbox GROUP BY status` (lines 181–183); loop writes `health[status] = int(count)` (lines 223–225); initialized `0` (line 158) | All rows whose `status == "pending"`, quarantine holds included |
| `claimable_pending` | `SELECT count(*) FROM outbox WHERE status='pending' AND {_QUARANTINE_CLAIM_GUARD}` (lines 202–205); filled at lines 229–231 | Pending rows **not** held by an operator quarantine marker; the guard is the module constant `_QUARANTINE_CLAIM_GUARD` (line 32), the same guard used by the claim path (`_claim_next`, lines 493–497, 522–528) |
| `quarantined` | `SELECT count(*) FROM outbox WHERE claim_owner LIKE 'quarantine:%'` (lines 191–193); filled at line 226 | Operator holds across **all** statuses; a hold keeps its original status. Additive and independent of `total` |
| `failed` | Same `GROUP BY` query, row `status == "failed"`; initialized `0` (line 159) | Retry-pending depth (the retry bucket: rows still owed a delivery attempt) |
| `dead` | Same `GROUP BY` query, row `status == "dead"`; initialized `0` (line 162) | Exhausted bucket: rows that hit `max_retries` and were released `dead` (`flush()`, lines 610–615, 629–630) |
| `claimed`, `delivered` | Same `GROUP BY` query (lines 160, 161) | In-flight (claimed under a lease) and terminal-success rows |
| `total` | Initialized `0`, incremented for every `GROUP BY` row (lines 169, 225) | Row count of `outbox` (partitions by `status`) |
| `oldest_pending_at`, `oldest_pending_age_s` | `SELECT MIN(created_at) ... WHERE status IN ('pending','failed','claimed')` (lines 184–187); age at lines 232–244 | Deliverable-age / staleness; `ok` flips `False` only on the stale branch (threshold `STALE_READINESS_S`, line 28/150) |
| `status`, `ok`, `reason`, `stale_after_s`, `db_path` | State machine + constants, lines 149–152, 213–248 | Probe verdict, not coordination state |

The five status buckets are the vocabulary `STATUSES = ("pending", "failed",
"claimed", "delivered", "dead")` at `src/assistx/outbox_client.py:40`. The
"retry buckets" reported by the outbox are therefore the **`failed`** and
**`dead`** status buckets above plus the **`attempt_count`** distribution
described next; there is no dedicated `retry` key (see §4).

### 2.2 From the audit CLI — attempt distribution

`tools/audit_outbox_ro.py::_audit()`:

| Metric key | Exact source | Meaning |
|---|---|---|
| `attempt_distribution` | `SELECT {attempt_count}, COUNT(*) FROM outbox GROUP BY {attempt_count}` (lines 185–195); emitted in `_result()` (lines 101, 113, 243) | Map of attempt-count value → row count; detects rows wedged at a high attempt count (the 2026-11 incident was 78 rows at `attempt_count=9`, see `src/assistx/outbox_client.py:592`) |
| `status_distribution` | `GROUP BY status` (lines 169–181); emitted at lines 100, 112, 242 | Same status partition as the health buckets, from the independent read-only connection |
| `claim_owner_counts` | `GROUP BY claim_owner` (lines 199–216); emitted at lines 102, 114, 244 | Quarantine markers are rendered; **every other owner is `<redacted>`** by `_owner_label()` (lines 74–81) |
| `total_rows`, `pass`, `detected_columns`, `db`, `size_bytes`, `mtime` | `_result()` lines 106–116, 165, 230–247 | Audit metadata; `pass` is false on unreadable/missing table, **not** a health verdict (module docstring lines 22–24) |

Invariants from the code: `sum(attempt_distribution.values()) == total_rows`
and `sum(status_distribution.values()) == total_rows` (each is a `GROUP BY`
partition of the same table). `attempt_count` itself is an `OutboxEntry` field
and an `outbox` column (`src/assistx/outbox_client.py`, lines 79, 89, 102, 301);
the health probe never aggregates it.

### 2.3 Internal consistency identity

From `docs/outbox-health-key-semantics-r14.md` §"The
`claimable_pending + quarantined == pending` identity" and the code above:

```
claimable_pending + quarantined
  = pending + count(quarantine-marked rows whose status != 'pending')
```

The equality `claimable_pending + quarantined == pending` holds **exactly when
every `quarantine:%` marker is on a `pending` row** — the operator-hold
convention (a hold keeps the original status). It is not universal: a marker on
a `failed`/`claimed` row makes the left side exceed `pending`, and both counts
remain correct because `quarantined` is deliberately status-independent.

## 3. What the outbox CANNOT report (separate store / separate identity)

- **MCP session identity.** There is no session key in either surface. The
  health probe exposes only counts and the opaque `claim_owner` marker test.
  `_validate_claim_owner()` (`src/assistx/outbox_client.py`, lines 52–70) lets a
  caller write a short printable label (e.g. an MCP session id) into
  `claim_owner` via `_claim_next(owner=...)` (lines 461–490), but it is rejected
  if it uses the `quarantine:` prefix and is treated as an **opaque fence
  token**, not an authenticated principal. `tools/audit_outbox_ro.py::_owner_label()`
  (lines 74–81) renders every non-quarantine owner as `<redacted>`. The
  three-plane boundary and "no durable MCP identity" are pinned in
  `docs/mcp-outbox-bridge-r22.md` §1 and §4. The outbox cannot say *which*
  session holds a claim, only that a row is claimed or quarantine-held.
- **Graph-side utilization / MCP coordination capacity.** The graph lives in a
  separate SQLite file (`SG_ARCHIVE_DB`) and the optional coordination DB in yet
  another (`SG_COORD_DB`). `sg_agent_list`/`sg_observations` are separate read
  tools whose results are `coordination_db_absent_or_unconfigured` while
  `SG_COORD_DB` is unset (`docs/mcp-outbox-bridge-r22.md` §1, §3). The outbox DB
  contains none of those tables and neither read-only surface queries them. The
  admission cap 8 / 32-reader–1–2-writer envelope recorded in
  `docs/mcp-outbox-bridge-r22.md` §3 and `FLEET-ITERATION-STATUS-20261126.md`
  Round 26 is a graph-side DESIGN/measurement fact, **not** an outbox metric;
  throughput is NO DATA. Do not infer one store's load from the other's keys.
- **Cross-store atomicity.** The outbox and graph share no transaction, lock or
  token (`docs/mcp-outbox-bridge-r22.md` §1). A busy error or depth spike in one
  says nothing about the other.

## 4. Keys that do NOT exist — do not invent them

- `health["retry"]`, `health["retry_buckets"]` and `health["attempt_buckets"]`
  are **not** produced by `read_queue_health()` at this revision. This matches
  `docs/outbox-health-key-semantics-r14.md` §"Keys that are NOT returned" and
  `tests/test_outbox_health_key_semantics_r14.py` (lines 156–157). The retry
  signal in health is the `failed`/`dead` status buckets; attempt detail is the
  audit CLI's `attempt_distribution`, a different surface.
- `OutboxClient.get_stats()` (`src/assistx/outbox_client.py`, lines 671–689)
  returns only `total` plus the five `STATUSES`; it has no quarantine, claimable
  or attempt buckets and is not a health key set.

## 5. Validation reference

`tests/test_outbox_coordination_metrics_r26.py` (in-memory SQLite only; no
production change) builds one mixed fixture with quarantine markers, session-
id-shaped (non-hold) owners and retry/terminal rows, then asserts: (a) every
health key from §2.1 is present and no invented key appears; (b) the
`claimable_pending + quarantined == pending` identity and the
`total == sum(status buckets)` invariant; and (c) the audit CLI's fixed JSON
key set contains `attempt_distribution`/`status_distribution` while non-hold
owners are redacted. It is a contract test, not a capacity measurement.
