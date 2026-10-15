# Context event authority — disposable SQLite atomicity and rollback counterexample (2026-10-15)

**Security decision: RESEARCH PASS (23 focused) / PRODUCTION P0 NO-GO.**

## Lineage and exact scope
- Stacked on [Auto-Assist draft PR #376](https://github.com/scottjoyner/auto-assist/pull/376), exact parent `7d5a085ff587705167030f9061da96be3e921b43`, which hosted three real-`swarm_core.record_event` **fake graph** negative tests. Previous PR376 CI on that exact head was GREEN with 787 non-integration tests and 59 integration cases deselected, but that earlier CI must **not** be cited for this new head.
- Research-only standard-library SQLite implementation `tests/sqlite_event_effect_lab.py` and isolated `tests/test_sqlite_event_effect_lab.py`, **no runtime module import, API, worker, driver, broker, graph migration, credentials, network, Docker, fleet restart or production effects**.
- The `sqlite3` file exists only under `pytest.tmp_path`. An actual SQLite file transaction guards two tables, `operations` and `effects`, while `fenced_scope` stores a disposable epoch and revision. SQL uses `BEGIN IMMEDIATE`, unique per-operation IDs and a single commit of operation, effect, and revision. A semantic digest pins `actor`, `action`, `target`, canonical JSON payload and digest version, rather than unstable transport fields such as a timestamp or regenerated event ID. This is **one database, one administrator, one transaction**; the test does not use physical Neo4j or demonstrate distributed all-or-nothing commit.

## Executed positive evidence
The real, disposable SQLite engine (not a mocked DB) was exercised with **23/23 tests passing /0 failed** in the same `tests/` sibling-import layout as published. `ruff check` reports **all checks passed**. The following cases were executed:

| Gate | Verified outcome |
| --- | --- |
| Same semantic op repeated | First `COMMITTED`, subsequent exact duplicates `DUPLICATE_COMMITTED`, one effect row, one revision increment |
| Same key / changed payload or actor | `CONFLICT`, no second effect |
| Eight threads submit one key | Exactly one commit, seven dedupe receipts |
| Four separate operating-system processes submit one key | Exactly one commit, three dedupe receipts |
| Two processes submit conflicting payloads under one key | One commit / one conflict |
| Injected exception after operation reservation | Full SQL rollback; zero ops, zero effects, unchanged revision |
| Injected exception after effect insert but before transaction commit | Full SQL rollback; zero ops/effects |
| Simulated HTTP ACK lost **after commit** | New DB connection recognizes exact committed duplicate; one effect |
| Distinct new key with stale expected revision | `STALE_REVISION_HOLD` |
| Old term after synthetic term advance (including duplicate ACK) | `FENCED_HOLD`; not ACKed |
| Committed operation whose effect receipt is absent | `UNCERTAIN_HOLD`, no ACK |
| Unsupported/noncanonical identity/key/JSON | Denied before transaction |

**Research API discipline:** the fixture exposes `may_ack` solely for exact committed/read-back matched effects, never `may_retry=True`; it does not authorize network effect retries, production service callers or Neo4j Bolt writes.

## Required negative proof — same-admin rollback
An **entirely disposable same-admin SQLite file backup** was captured before synthetic commit. After an accepted effect, the test deliberately restored that prior file over the temporary DB. Replaying the identical valid operation then **committed again**: a second logical commit while the restored file shows only one effect. This is a P0 rollback-replay counterexample, **not a production database restore, database audit, actual external Neo4j graph effect, physical power loss, WAL restore or independent authority failure**. It proves a same-admin spent/term ledger—even when transactional—cannot be asserted rollback-resistant. No graph, storage array, credential or NAS modification occurred.

## Exact executed source identities
| File | Git blob (executed + published) | SHA256 |
| --- | --- | --- |
| `tests/sqlite_event_effect_lab.py` | `d7a58a1ccff54897f84b423da5521d146eb13d6c` | `be954b4a3ec47aa7a55310b756c5503607490a3ffd826873d5df13ea22ffd194` |
| `tests/test_sqlite_event_effect_lab.py` | `efdebc31bf59ef1cf865a202dd80e521c2fc5192` | `48d37f57833c5c7f5a3502c828fd9427395c8c09c795a85ff78a54cbc92e8cce` |

Reproducer from an exact checkout containing these research files:
```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/test_sqlite_event_effect_lab.py
ruff check tests/sqlite_event_effect_lab.py tests/test_sqlite_event_effect_lab.py
```
These 23 tests run in a disposable SQLite lab. Full hosted exact-PR-head CI and a genuinely isolated Enterprise Neo4j lab are separate gates.

## Implications for AssistX and ContextGraph design
The baseline AssistX `swarm_core.record_event` stores the envelope and then runs `reconcile_event` separately. PR #376 reproduces that a projection failure can be followed by a replay that returns `accepted=True/deduped=True/graph_reconciled=False` **without trying projection again**, and its forced read-before-CREATE interleaving admits two fake envelopes with one logical key. The current Neo4j schema source only establishes `UNIQUE EventEnvelope.event_id`, not an authenticated effective uniqueness constraint for `idempotency_key`.

The SQLite lab is a **model of acceptance properties**, not an implementation ready to transplant into Neo4j. A safe resource-side adapter requires at minimum: (a) unique canonical op key within a separately governed trust domain; (b) operation+scope+term+digest identity; (c) an effective resource-side atomic commit witness; (d) an independently governed rollback-resistant spent-effect/highwater anchor; (e) explicit post-projection completion attested by the actual resource, never envelope-only existence; (f) no automatic effect retry on timeout, partition, lost ACK, weak `known` status, bare 409 or missing `applied`; (g) exclusive writer and break-glass governance with negative direct Bolt write proof.

For graph effects that **cannot** share one resource transaction with the operation authority, copy/pasting SQLite `BEGIN IMMEDIATE` is invalid. Such effects need a separately specified coordinator/reconciliation contract, fencing, source/term custody, resource-side receipts and tested **UNCERTAIN** outcomes. A service-side SQL or HTTP preflight alone remains bypassable.

## Frozen boundaries
- Crypto [#46](https://github.com/scottjoyner/kipnerter-api/issues/46) OPEN; graph RBAC [#21](https://github.com/scottjoyner/kipnerter-api/issues/21) OPEN; production [#31](https://github.com/scottjoyner/kipnerter-api/issues/31) HOLD; hosted Context exact-head CI [#45](https://github.com/scottjoyner/kipnerter-api/issues/45) OPEN.
- [Auto-assign draft #17](https://github.com/scottjoyner/auto-assign/pull/17) (explicit applied true) and [#18](https://github.com/scottjoyner/auto-assign/pull/18) (409 is not delivery) remain unmerged and do not prove upstream atomic operation ownership.
- **No merge, deployment, production graph/data, Neo4j grants, credential rotation, network changes, physical NAS/device actions, automatic retries, effect promotion or failover.**
