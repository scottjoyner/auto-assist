# B01 — disposable Neo4j-native Ed25519 claim resource

**Date:** 2026-10-14, x1-370, Fleet Commander. **Disposition:** `PASS_SYNTHETIC_WITH_GRAPH_RESTORE_NO_GO`. **PRODUCTION HOLD.** Stacked on [Auto-Assist draft PR #359](https://github.com/scottjoyner/auto-assist/pull/359) at GitHub head `bc6de083762aad7af22178f66f20a61b9c135360`. Separate research from [Context issue #46](https://github.com/scottjoyner/kipnerter-api/issues/46). Nothing here changes a live Neo4j plugin, schema, role, service, network, signed key, TPM, credential, NAS, API, failover, or deployment.

## What was newly proved

The preceding synthetic signed HTTP gateway validated Ed25519 outside Neo4j. A stolen *gateway's graph-writer credential* still issued arbitrary Cypher, setting Task status without signed evidence. This PR tests a different resource boundary: a **Neo4j Enterprise procedure** that performs the two Ed25519 verifications **inside the same database transaction as the fixed Task mutation**. Its ordinary caller has no direct `SET`, `CREATE`, or `DELETE` privileges; it can only call the specifically named `b01research.claimFixture` with elevated procedure execution. The feature is supported by Neo4j Enterprise's documented `EXECUTE PROCEDURE` plus `EXECUTE BOOSTED PROCEDURE` privileges. Elevation applies to the procedure body, not arbitrary Cypher outside it. The procedure is **UNREVIEWED JAVA research code**: boosting execution of an untrusted procedure is itself a powerful risk.

`tests/native_b01/FixtureSignedClaim.java` uses the JDK `Ed25519` signature verifier, two different lab public roots pinned in the **disposable graph container environment** (not caller-provided), exact synthetic instance, Task, agent, operation, term, next sequence, unique nonce, policy digest and 60-second maximum evidence TTL. It takes a Neo4j transaction write lock on the synthetic `FixtureFence` node before checking and advancing graph-local high-water, then changes one `FixtureTask READY→CLAIMED` with the same transaction. Any invalid condition aborts the transaction; the return values contain no private signatures or sensitive Task payloads.

The client protocol uses a fixed ASCII/newline-delimited `B01-NATIVE-CLAIM-v0` wire message over strictly regex-bounded fixture fields. **No production identities** or `Task` labels are accepted. Keys are newly generated within the single local test process; no independent remote witness/custody is claimed. Root pinning to container startup environment is outside *graph-only restore*, but remains within a privileged container administrator's control.

## Repeatable local disposable physical acceptance

```bash
cd /home/scott/git/wt-assistx-b01-neo4j-procedure-20261014
PYTHONPATH=tests python3 -m pytest -q tests/test_trace_b01_native_claim_protocol.py
ASSISTX_B01_NATIVE_CRYPTO_PHYSICAL=1 python3 tests/probe_trace_b01_neo4j_native_ed25519_physical.py
```

The runner compiles the Java source using **only the already cached Neo4j 5.26 Enterprise JDK image**, offline with an ephemeral bind-mounted compile scratch; it does not publish or commit a compiled JAR. It then copies the JAR only into a **fresh, uniquely named disposable Neo4j** container, before that fixture starts. Docker network is `--internal`, with **zero published host ports, zero persistent graph mounts** and ephemeral passwords/roots. Exactly one synthetic role gets `ACCESS`, fixture `MATCH`, `EXECUTE PROCEDURE b01research.claimFixture` and `EXECUTE BOOSTED PROCEDURE b01research.claimFixture`; it receives **no graph `SET`/`CREATE`/`DELETE` grants**. The fixture is torn down in the runner's `finally`.

### Physically observed (local research, exit 0)

- Dual-signed issuer+device proof with correct Task/owner/policy/instance, term 1 and next high-water **committed exactly once**.
- Forged issuer or device Ed25519 signatures, foreign owner, wrong policy or wrong instance rejected with **no graph state changes**.
- That **same credential** attempting direct Cypher `SET`, `CREATE`, and `DELETE` was denied. It is therefore not an arbitrary graph-property writer despite the narrow boosted procedure grant.
- Repeated valid signatures and stale sequence denied. Two separately signed and concurrent next-sequence claims produced **one winner** and only one new Task claim.
- Fixture-admin term turnover 1→2 rejected a signed old-term claim; a fresh current-term signed claim succeeded.
- **Critical NO-GO reproduced:** resetting ONLY graph-local term, high-water, used nonce history and Task state allowed the **same legitimate term-1 signatures to be accepted again**, despite unchanged pinned public keys. Native Ed25519 does **not** supply rollback-resistant monotonic custody by itself.

The focused source tests verify canonical message equality, signer-role separation, malformed fields, native call ordering, confinement to fixture labels, opt-in Docker isolation and cleanup. Full B01 staged research regression: **244/244 passed**, exit 0 before publication. Exact published GitHub head and CI must be independently checked.

## Boundaries that cannot be promoted

1. **Privileged Neo4j administrator/host compromise:** admin still controls graph records and native procedure JAR. The ordinary caller role is only a synthetic constraint; no production role was changed. There is no independent immutable resource whose admission a Neo4j superuser cannot bypass.
2. **Rollback and consent:** the graph-local high-water and nonce register are restorable together. Real hardware/quorum immutable high-water, signer trust root governance, device origin, policy and consent verification, signature expiration under clock manipulation, restoration and root-rotation semantics are unproven.
3. **Exact effect/uncertainty:** only a single synthetic claim statement was tested; no durable independent post-COMMIT witness, cross-resource atomicity, uncertain in-flight recovery, durable expired nonce retention, heartbeat, complete, compensation or split-brain acceptance was established.
4. **Production network bypass:** seven real non-Neo4j containers previously demonstrated direct Bolt/HTTP TCP reachability and there are wildcard published Neo4j host ports. No live graph/network/credential cutover has been attempted or authorized.
5. **Security review:** the Java implementation is a small unreviewed lab procedure loaded only into a disposable graph. Production extension configuration, native dependency/ABI, privilege elevation attack surface, key/host custody, stable packaging and independent code review remain open.

**Remain DRAFT/UNMERGED, B01/B02/B06 HOLD, production admission/failover/redeployment disabled.**