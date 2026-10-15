# Context P0 — native resource-side effect fencing and privileged bypass (2026-10-15)

**Disposition: 17/17 executable resource-transaction research tests PASS; production Context effect promotion remains P0 NO-GO.** This is a SQLite fixture, not a deployed AssistX/Neo4j adapter.

## Continuity
Stacks on [Auto-Assist draft #381](https://github.com/scottjoyner/auto-assist/pull/381) exact parent SHA `724bbb6eb76d9567bddc6a46b8244beb73f29325`, paired with [knowledge draft #167](https://github.com/scottjoyner/knowledge/pull/167) head `829d8df61e1844492286b5fb8c77a7aefcc23cc6`. Prior #381 established that a separate same-admin spent-ledger can detect selective rollback but both-domain restore, stale readback and direct resource writes remain P0. [API draft #104](https://github.com/scottjoyner/kipnerter-api/pull/104) showed that fresh recovery-term certificate readback still permits resource rollback / after-read TOCTOU. Existing `context_tests/test_postcommit_challenge_research.py` also already reproduces a verifier restart yielding a second logical ACK; no duplicate challenge/signature lab was built here.

## What was executed, exactly
One additive, test-only file `tests/test_resource_effect_trigger_boundary_research.py`, Git blob **`b0b9a5a620d300f735b605807c0d7c843746d554`**, SHA256 `24242f4c36221eba7db88c174c5f8b0d7185ad80fd12c2ff06b3d0a25d153832`. Native Python `sqlite3` uses a disposable file created under `pytest.tmp_path`, no URI to a live database.

The fixture implements **one resource-side SQL transaction** with a `BEFORE INSERT` trigger verifying current `term_head.term` and a matching permit's `op_key + payload_digest + term + scope`. An `AFTER INSERT` trigger writes a 16-byte random commit nonce and matching effect identity to the `receipts` table **in the same transaction**; a rollback removes both rows. `effects.op_key` is UNIQUE. Multiple threads use distinct actual SQLite connections, not a fake session.

### Positive resource-boundary evidence

| Native test | Result |
| --- | --- |
| Permitted synthetic operation inserted | Exactly one effect and matching trigger-generated receipt |
| No permit; wrong key; wrong digest; stale term | Actual SQLite trigger rejects INSERT |
| Term revoked after Python preflight but before effect INSERT | **INSERT rejected at mutation time** |
| Six concurrent valid writers of one operation | One COMMIT, five duplicate denials, one receipt |
| Receipt row created then transaction rolled back | **No effect and no receipt** |
| Committed ACK lost, same-key retry of SQL INSERT | No second effect; existing receipt unchanged |
| Term update on separate connection while effect transaction holds resource writer lock | SQLite serializes revocation **after** effect transaction (no midtransaction term change) |
| Distinct operations | Different 128-bit nonce values generated inside their effect transactions |
| Missing SQLite resource during readback | Read-only connection fails rather than silently creating an empty ledger |

All assertions are about the **same disposable database** under **one OS/operator/DBA**. A concurrent old-term effect could already have committed before a later term change; its matching readback is therefore still **UNATTESTED**, not a current effect ACK.

### Deliberately passing P0 negative counterexamples

1. **Privileged trigger removal**: same SQLite administrator DROPs the permit-check trigger, inserts an effect without authorization, and still obtains a matching trigger-generated receipt. A second test drops **both** triggers and fabricates perfectly matching effect and receipt records. Thus `MATCHED_UNATTESTED` is **not** proof of authenticated admission.
2. **Permits are not independently governed**: the same database principal can INSERT a forged permit then execute an effect. Term and digest matching inside one resource transaction cannot prove original source consent.
3. **Privileged restore replay**: after an effect commits, the same operator restores a disposable precommit SQLite file; the identical operation can then COMMIT a second logical time although only one current effect remains.
4. **Post-COMMIT readback race**: deleting/changing a receipt after a previous readback leaves a stale nonce snapshot. An in-resource trigger does not make external after-the-fact ACK finalization atomic.

## Test identity and command
Physical Fleet Commander x1-370:
```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  context_tests/test_resource_effect_trigger_boundary_research.py
ruff check context_tests/test_resource_effect_trigger_boundary_research.py
```
**17 PASS /0 FAIL; Ruff all checks passed.** Test log SHA256 `f525d39ed2c486806c7c6d4a663f9a300596593a384d5171e20346ea2e64038a`.

This branch adds no runtime source, remote connection, signing implementation or production Graph API. Its first hosted exact-head CI result must be checked separately. The older Context API hosted [issue #45](https://github.com/scottjoyner/kipnerter-api/issues/45) remains blocked before runner allocation.

## Smallest safe next acceptance
1. In an independently **approved, disposable Neo4j Enterprise** resource, test actual authenticated privilege readback: legacy API/worker/Hermes direct `CREATE/SET/DELETE` denied, broker permitted only to required ContextGraph labels/relationships, DBA break-glass separately custodied. Do not reuse existing running graph/credential or bypass blocked fixture provisioning.
2. Make the graph *effect itself* carry atomic operation ID/digest/current term/revision and resource-owner commit proof. A SQLite trigger, query preflight or signing key owned by that same administrator is insufficient.
3. Verify independent source/resource Ed25519 roots at the resource mutation boundary, with a separately administered, rollback-resistant consumed-key/high-water anchor. Deny forged signed postcommit fiction, key collusion, revocation and dual-restore replay.
4. Treat lost ACK, queued effect, unknown COMMIT, resource partition and postreadback status changes as **HOLD, never automatic replay** until an authoritative resource receipt can be independently reconciled.
5. Exact-head hosted CI and independent security review must precede any promotion.

**P0 unchanged:** [Context cryptographic #46](https://github.com/scottjoyner/kipnerter-api/issues/46) OPEN; [RBAC #21](https://github.com/scottjoyner/kipnerter-api/issues/21) OPEN; [production graph #31](https://github.com/scottjoyner/kipnerter-api/issues/31) HOLD; [CI #45](https://github.com/scottjoyner/kipnerter-api/issues/45) OPEN. No merge, live graph effects, GRANT/REVOKE, credentials, network, NAS/device, deployment, failover or automatic retry.

## Exact executed GitHub Actions results
At Auto-Assist research commit `09f068426328153275cdc2cb2aef0741013ab227`, hosted [Actions 38752017844](https://github.com/scottjoyner/auto-assist/actions/runs/38752017844) and [Actions 38752017889](https://github.com/scottjoyner/auto-assist/actions/runs/38752017889) both completed **SUCCESS**. The unit job executed the normal non-integration suite: **857 PASS /59 DESELECTED /9 WARNINGS**; recovery-canary and handoff validation also passed. Thus all 17 new research tests were incorporated into successful hosted execution. The later report-only commit does not change the tested source Git blob `b0b9a5a620d300f735b605807c0d7c843746d554`; any CI attached to a later documentation head is a separate result. Passing research CI is NOT production Neo4j or independent signer authority acceptance.
