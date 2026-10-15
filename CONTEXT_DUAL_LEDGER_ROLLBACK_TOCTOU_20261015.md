# Context P0 — dual-ledger rollback, lost-ACK and effect-time TOCTOU (2026-10-15)

**Result: 30/30 focused two-ledger tests PASS; 53/53 disposable composite PASS; P0 NO-GO.** Source is a test-only safety counterexample, not a resource-side broker, trusted remote authority, or production fix.

## Parent chain and scope
Stacks on [Auto-Assist draft #379](https://github.com/scottjoyner/auto-assist/pull/379) exact head `c96e789467270c750d17c742fb02aea6e78954d6` and [knowledge draft #162](https://github.com/scottjoyner/knowledge/pull/162) exact head `572ced553278f54133c7415c25c3807c71dc2355`. Parent #379 demonstrated real single-SQLite transactional operation+effect atomicity and a same-admin backup/restore replay negative, with 810 hosted unit tests and 59 deselected on its tested source revision. **Do not infer its CI results apply to this new head.**

New code is **exclusively** `tests/dual_authority_lab.py` and `tests/test_dual_authority_lab.py`. Both are standard-library Python+SQLite research fixtures under pytest's disposable `tmp_path`, physically executed on x1-370, with no Docker, SSH, Neo4j, real graph, fleet topology, credentials, production access, network, deploy or physical NAS modifications.

## Domains and meaningful safety semantics
- **Authority**: disposable SQLite file #1, `term_head` and `claims` keyed by operation, digest and term; `reserve()` atomically serializes duplicate claims. A separate method `observe()` attempts to read the effect and labels the claim `OBSERVED`. It has **no independent signer, immutable storage, admin separation or cryptographic post-COMMIT attestation**.
- **Resource**: separate disposable SQLite file #2 with effects indexed by operation key and payload digest. The deliberately exposed `apply_direct()` shows that the resource writer **can bypass** the authority completely. There is no distributed transaction or real external resource ID.
- **Reconciler**: read-only checks authority term/claim and resource effect independently. Missing files are opened `mode=ro` and **not recreated**. Every result remains `decision=HOLD`, `may_ack=False`, `may_retry_effect=False`, `may_promote=False`. Matching records are reported **CONSISTENT_UNATTESTED**, not a verified COMMIT.
- Each file is in a separate disposable folder, but **both share the same operating system, runtime account and human operator**. They are two *logical* ledger domains, **not independently governed administrative custodians**. They cannot prove any remote antirollback security.

## Executed checkpoints and findings

| Synthetic physical SQLite sequence | Actual observation | What it does / does not prove |
| --- | --- | --- |
| Six concurrent guarded `reserve()` calls with same key | 1 `PREPARED_NEW`, 5 `PREPARED_EXISTS` | Atomic reservation in one local authority file; no effect authorization |
| Guard reservation, no resource write | `PREPARED_UNCERTAIN` | Never automatically reissue an effect |
| Resource write, authority still PREPARED | `EFFECT_UNACKED` | Potential lost ACK / split commit, always HOLD |
| Both files contain same key/digest and authority marks observed | `CONSISTENT_UNATTESTED` | Matching readback is **not** a separately signed resource post-COMMIT |
| Restore only resource file to precommit state | `RESOURCE_ROLLBACK_SUSPECTED`; guard still rejects new reservation for same key | A surviving spent marker **can detect this selective rollback**; no evidence of hard effect-time enforcement |
| Restore both authority and resource precommit files | **Old operation returns `PREPARED_NEW` and can write another logical effect** | P0 antirollback counterexample: same administrator can rewind both domains |
| Write resource effect without reserve | `ORPHAN_EFFECT` | P0 direct resource-owner bypass remains possible |
| Missing authority/resource file | `AUTHORITY_UNAVAILABLE` / `RESOURCE_UNAVAILABLE` | Readback never silently initializes a new empty authority |
| Changed digest or obsolete term | `CONFLICT` / `TERM_FENCED` in selected admission paths | Source-level identity check, not independent authenticated signer grant |
| Resource row deleted **after** `observe()` sampled it but **before** claim marked OBSERVED | Authority marks `OBSERVED_UNATTESTED`; subsequent readback detects `RESOURCE_ROLLBACK_SUSPECTED` | **Effect-time TOCTOU FAIL**: check and marking are cross-domain, not an atomic resource witness |
| Guard term advanced from 1 to 2 between reservation and observe | `observe()` still marks old claim OBSERVED; querying with old term fences, but query with current term can show `CONSISTENT_UNATTESTED` | **Term binding / stale-claim transition FAIL**. Still no ACK, but consistent state is insufficient |
| Missing effect after PREPARED and simulated lost ACK | `PREPARED_UNCERTAIN` or `EFFECT_UNACKED` | No automatic retries or claims of external effects |

The final two negative tests intentionally demonstrate vulnerable source behavior; a green negative test means **failure reproducibly exposed**, not mitigated.

## Exact tested source and tools
| File | Git blob (physically executed) | SHA256 |
| --- | --- | --- |
| `tests/dual_authority_lab.py` | `5d12dcc44818c02fa9170d3d07c7ad3332bc26e5` | `a65dfff36fcc35c8cfa37d154951adf53c38b89d35fbefe2a90528f26c3dd56e` |
| `tests/test_dual_authority_lab.py` | `425416877f3782b871ca1c3b42b12486e231350e` | `bacb11d24517f8f8fc033d5e58ffca784aee6b159103030b81ff7dda59ca81bf` |

Command from the published research checkout:
```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/test_dual_authority_lab.py
ruff check tests/dual_authority_lab.py tests/test_dual_authority_lab.py
```

x1-370 native standalone suite: **30 PASS / 0 FAIL**, Ruff clean. Same directory shape as PR `tests/` plus previous #379 research lab: **53 PASS /0 FAIL**, Ruff clean; log SHA256 `7df3a4be79ffa27b45290d3af9ef3960df7783be5e542f30a44c5829cbc2b9b7`. That local test execution is not hosted exact-head CI; the new draft PR's hosted runs need separate verification.

## Smallest next physical acceptance
1. Independent authority **administrative and cryptographic custody**: pinned root trust, resource-side Ed25519 verifier, independently controlled monotonic committed-operation key/epoch and replay/highwater; test *both* file/resource restoration and wrong-root/colluding-key fiction with deny.
2. Actual isolated Enterprise Neo4j resource commit witness and graph model with scoped broker; test that a source signature alone cannot invent a post-COMMIT effect and that a direct legacy API/worker/Hermes Bolt writer is denied. Privileged owner/DBA break-glass paths require separate custody/audit; naive grant restrictions cannot defend against same DBA.
3. Fail-closed uncertain-commit state machine: authority PREPARED but missing or completed resource effect must be held for explicit operator reconciliation, never automatic replay; detection of stale epoch at **effect time**, not simply at subsequent readback.
4. Physical authenticated negative tests of concurrent same-key/conflicting-payload, lost ACK after committed effect, selective resource restore, independent authority rollback, quorum/partition, and *real* post-COMMIT evidence. Both domains need a tested coordinator protocol; SQLite two files do not provide cross-domain atomicity.
5. Hosted exact-head executed CI and independent review before merge/deployment.

**Gates frozen:** [RBAC #21](https://github.com/scottjoyner/kipnerter-api/issues/21) OPEN; [cryptographic #46](https://github.com/scottjoyner/kipnerter-api/issues/46) OPEN; [production #31](https://github.com/scottjoyner/kipnerter-api/issues/31) HOLD; [CI #45](https://github.com/scottjoyner/kipnerter-api/issues/45) OPEN. No merge, production graph/grants, credentials, Docker/network topology, physical devices/NAS, deployment, promotion, failover, or automatic retry.
