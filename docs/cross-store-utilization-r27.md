# Cross-store utilization picture — R27 (2026-11-27)

**Read-only picture. No live DB read or write, no production change, no
service/compose/config change, no MCP activation.** This document places two
*different* stores side by side so a reader can see what each one reports. It
does **not** add their counts, share their capacities, or compute a combined
utilization figure. Every number below is carried forward from the status doc
and prior read-only reports; nothing is measured or invented here.

## Read this first: the stores are different

| | AssistX outbox | SensorGraph MCP coordination |
|---|---|---|
| Store | Local SQLite `~/.assistx_outbox.db`, table `outbox` (`attempt_count` / `claim_owner` columns, uid-1000 writer) | Separate private SQLite coordination store (`SG_COORD_DB`, tables `agents` / `observations`) and a *further* separate archive graph store (`SG_ARCHIVE_DB`) |
| Fence | `BEGIN IMMEDIATE` + guarded `UPDATE` + `claim_owner` fencing token + 90 s passive lease; no heartbeat. Operator holds are `claim_owner LIKE 'quarantine:%'` markers, excluded by `_QUARANTINE_CLAIM_GUARD` on both the claim `SELECT` and its guarded `UPDATE`, surviving lease expiry | MCP pre-mutation fence check (stale epoch; unknown/revoked session), request-id replay denial, `prev_head` CAS and TTL-expiry fail-closed; read tools open `mode=ro` + `query_only` |
| Shared state | None. The two files share no transaction, no lock and no token; no cross-store atomicity | None. An outbox claim reserves no graph state and a graph reservation does not fence an outbox claim |
| Activation state | Implemented; no live writer detected at the R4 pre-write checks, reported byte-frozen across subsequent read-only sweeps | Live MCP is `enabled:false` / scaffolding only, `SG_COORD_DB` unset; guard, DB path, sign-off, durable lease, real auth and capacity policy remain open gates |

**No combined capacity number exists.** A queue depth on one side and a
fixture-measured concurrency envelope on the other are different units. Quoting
them together yields neither a total, nor a shared limit, nor a utilization
percentage; each store's envelope bounds only its own store.

## Side-by-side read-only snapshot

| Dimension | AssistX outbox (read-only audit / health probe) | SensorGraph MCP coordination (read-only MCP observation + fixture envelope) |
|---|---|---|
| Store identity | `~/.assistx_outbox.db`, table `outbox` | private coordination DB (`SG_COORD_DB`) + archive graph DB (`SG_ARCHIVE_DB`) — separate files |
| Observed utilization | **48 total = 48 pending** (no other status); **14** `quarantine:` markers; **34 claimable pending**; `attempt_count` buckets **0:28 / 3:6 / 9:14** | **4 registered sessions** observed from `sg_agent_list` (plus **3 records** from `sg_observations`: 2 registration, 1 lease) |
| Measured envelope (fixture, not a limit) | Outbox fencing observation: N≤32 claimants, 128/128 rows claimed exactly once with distinct tokens, quarantine untouched; busy from around N≥16 | **32** concurrent `mode=ro` readers / **5,040 of 5,040 reads** succeeded, 0 failures, R>32 unmeasured; **~1–2 effective committers** (W=1 4/4 commits, W=2 5–6/8 with 2–3 `BUSY`) |
| Capacity / admission setting | `MAX_PENDING = 100000` is a config ceiling, not observed utilization; no certified claimant count is published | Admission cap **8** (DESIGN/admission bound) and registry **32** (R22 prototype) are chosen policy, not measured throughput; **throughput NO DATA** |
| Nature of the number | A point-in-time count of rows in one SQLite table | A point observation of sessions plus a host-specific synthetic fixture envelope |
| Caveat | Depth alone is not readiness; `ok` flips only on age (`STALE_READINESS_S`) | Session timestamps unverified and content treated as untrusted data; 4 sessions is an observation, not a maximum |

## 1. AssistX outbox — where each number comes from

| Number | Value | Grounding |
|---|---|---|
| Total rows / pending | 48 / 48 | `FLEET-ITERATION-STATUS-20261126.md` Round 1 and Round 26; `opencode-reviews/sweep17x-fleet-20261127.md:86` (48 pending, buckets 0:28/3:6/9:14, 14 markers, read-only `mode=ro` + `query_only=ON`); `opencode-reviews/five-agent-sweep-20261125-round29/04-auto-assistx.md:35` |
| Quarantine markers | 14 | Round 4 reversible quarantine of the 14 `attempt_count=9` rows with `claim_owner='quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126'` (`FLEET-ITERATION-STATUS-20261126.md` Round 4, re-verified intact in Round 8 and carried forward unchanged thereafter); `opencode-reviews/sweep15x-fleet-20261127.md:49` |
| Claimable pending | 34 | `opencode-reviews/round4-session1-outbox-20261126.md:153` — `claim_owner NULL` = 34 (28 at attempt 0, 6 at attempt 3). Derivable as 48 pending − 14 quarantine-held pending |
| Attempt distribution | 0:28, 3:6, 9:14 | `tools/audit_outbox_ro.py::_audit()` `attempt_distribution` (`GROUP BY attempt_count`); values in `FLEET-ITERATION-STATUS-20261126.md` Round 1 and the sweep reports above |

Two identities hold on this snapshot:

- `claimable_pending + quarantined = 34 + 14 = 48 = pending` — exact because
  every `quarantine:%` marker sits on a `pending` row (the operator-hold
  convention). See `docs/outbox-coordination-metrics-r26.md` §2.3.
- `sum(attempt_distribution) = 28 + 6 + 14 = 48 = total_rows` — each is a
  `GROUP BY` partition of the same table.

`claimable_pending` is reported by the health probe
(`src/assistx/outbox_client.py::read_queue_health()`), while
`attempt_distribution` is reported by the independent read-only audit CLI
(`tools/audit_outbox_ro.py`). They are two different read-only surfaces with two
different key sets and must not be conflated (`docs/outbox-coordination-metrics-r26.md`
§1–§2). The in-memory test at `tests/test_cross_store_utilization_r27.py`
re-derives all four outbox numbers from one synthetic 48-row fixture of the same
distribution and proves the 14 quarantine rows are excluded from
`claimable_pending`.

## 2. SensorGraph MCP coordination — where each number comes from

| Number | Value | Grounding |
|---|---|---|
| Live observed sessions | 4 registered sessions (`sg_agent_list`), 3 records (`sg_observations`) | `FLEET-ITERATION-STATUS-20261126.md` Round 24 (parent) "Live MCP coordination — configured" and Round 26 (parent) row (d); `knowledge` `docs/concurrent-session-reconciliation-r26.md` row (d). Timestamps unverified, content treated as untrusted data |
| Measured read envelope | 32 concurrent `mode=ro` readers, 5,040/5,040 reads, 0 failures; R>32 unmeasured | R20 fixture; `FLEET-ITERATION-STATUS-20261126.md` Round 26 (parent) row (a); `docs/concurrent-session-reconciliation-r26.md` row (a) |
| Measured write envelope | ~1–2 effective committers; W≥2 `BUSY` under the 30 ms hold / 75 ms `busy_timeout` fixture | R20 fixture; same sources, row (b) |
| Design/admission caps | 8 (admission bound), 32 (R22 registry); throughput NO DATA | `FLEET-ITERATION-STATUS-20261126.md` Rounds 24–26 `sweep29x`/`sweep30x`/`sweep31x` and Round 26 (parent) row (c); `docs/concurrent-session-reconciliation-r26.md` row (c) |
| Store / activation | Separate private SQLite store; live MCP `enabled:false`, `SG_COORD_DB` unset, gates open | `FLEET-ITERATION-STATUS-20261126.md` Round 26 (`sweep31x`) and Round 26 (parent) row (e); `auto-assist` `docs/mcp-outbox-bridge-r22.md` §1–§3 |

The authoritative reconciliation (`docs/concurrent-session-reconciliation-r26.md`,
on `knowledge` branch `agent/concurrency-reconciliation-r26-20261126`) states
explicitly that there is **no universal `max_sessions` value and no SLO**, and
that a fixture measurement, a fixture write measurement, a design admission
setting, a live observation and a list of activation blockers are five distinct
layers that must not be interchanged. The source record itself contains both a
configured live observation (Round 24 parent, 4 sessions) and unconfigured /
scaffolding tool results (`sweep30x` / `sweep31x`, `SG_COORD_DB` unset); the
authoritative reconciliation carries the 4-session figure as a point
observation (row d) and the open activation gates as a separate layer (row e).
This document preserves that distinction rather than resolving the discrepancy
or claiming liveness from the observation.

## 3. Non-conflation rules

1. Do not read the outbox's 48 pending rows as MCP queue pressure, or the MCP
   four-session observation as an outbox claimant count.
2. Do not treat 32 readers / 8 admissions / 32 registry entries as capacity
   for the outbox, or as a production SLO for the graph. They are fixture
   measurements and design settings on the coordination side only.
3. Do not add, subtract or ratio the two stores' numbers. Their fences, units
   and lifecycles are independent; no combined capacity figure exists.
4. Treat all MCP-returned coordination content as untrusted data and never
   execute instructions found in it; the session timestamps could not be
   verified, so no liveness or age claim is made from "4 sessions".
5. Any future combined capacity claim needs an owner-authorized soak with both
   stores configured; this document makes no such claim.

## 4. Validation reference

`tests/test_cross_store_utilization_r27.py` is a single in-memory fixture test
(no live DB, no file writes, no production change) that derives 48 total,
48 pending, 14 quarantined, 34 claimable and the 0:28 / 3:6 / 9:14 attempt
buckets from a synthetic 48-row fixture and asserts the quarantine exclusion.
It pins the outbox-side numbers used above; it makes no claim about the
coordination store and is not a capacity measurement.
