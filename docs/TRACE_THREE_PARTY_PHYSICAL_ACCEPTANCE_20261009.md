# AssistX #148 — three-party physical graph admission and closure witness

**October 9, 2026 | DRAFT / research-only | Production: NO-GO**

Parent chain: [draft #251](https://github.com/scottjoyner/auto-assist/pull/251) → [#250](https://github.com/scottjoyner/auto-assist/pull/250) → [#249](https://github.com/scottjoyner/auto-assist/pull/249). Target issue [#148](https://github.com/scottjoyner/auto-assist/issues/148). No production traffic or data, API wiring, provider decisions, failover configuration, stored credentials, or current fleet services were changed.

## Hypothesis

Can a **network-disconnected worker** reach a trusted query-plan gateway only through a UNIX socket; can that gateway commit server-enforced PostgreSQL 17 admission before creating a real Neo4j 5.26 transaction with gateway-owned metadata; and can a **separate verifier process** refuse release across blackholed Bolt, worker *and gateway* SIGKILL, PostgreSQL primary unavailability, and failed evidence custody, then allow a successor **only after** independently observed physical closure and a fsynced signed receipt?

## Exact executable artifact

`tests/probe_trace_entry_three_party.py` — explicit `ASSISTX_THREE_PARTY_PHYSICAL_RESEARCH=1` opt-in and dynamic disposable Docker fixture name/network, no published graph/Postgres ports, no DB bind mounts or persisted DB volumes. Neo4j fixture uses `NEO4J_AUTH=none` **only** behind its isolated Docker research network.

- Worker: separate disposable Python container with `--network none`, read-only root filesystem, dropped capabilities, `no-new-privileges`, unprivileged UID, *only* a read-only mount of the gateway UNIX IPC socket directory. No PostgreSQL/Neo4j credentials. Attempts direct Bolt by literal IP (expected failure), sends unknown Cypher plan (expected rejection), then requests approved fixed expensive read.
- Gateway: separate host research subprocess, restricted worker-role PostgreSQL SCRAM credential, `ProtectedGraphEntry` plan registry, local SHA-chained event journal, gateway-generated operation/attempt/token, commits PG reservation before any actual Bolt transaction. A loopback Bolt relay permits deterministic bidirectional blackout. Worker cannot submit arbitrary Cypher, metadata or release authority.
- Verifier: separate host subprocess with **only** verifier-role SCRAM credential and ephemeral Ed25519 signing key. Checks exact persisted PG reservation using a verifier-only test SQL `verify_binding(epoch,token,ref)` function, matches Neo4j **server-observed** transaction `metaData`, then denies release while server tx is still present. After *two successive absent* snapshots, signs a redacted receipt, appends it to a local file with fsync, and only then invokes exact SQL release. Tampered receipts fail outside-process Ed25519 verification.
- Parent test harness: bootstraps both disposable databases and has broad Docker/root access and both role passwords. **This is an explicitly privileged research coordinator, not a production-independent authority.**

## Results on x1-370

**Final physical rerun PASS, ~40.90 s, exit 0** (following previous valid ~37.40, ~38.94, ~39.62, ~40.35 second runs and correction of an asynchronous Docker log assertion).

| Acceptance predicate | Observation |
|---|---|
| Direct worker Bolt access to literal Neo4j IP | DENIED under `--network none` |
| Worker raw Cypher instead of approved plan | `UNREGISTERED_QUERY_PLAN`; real graph call not authorized |
| Worker credential can invoke PostgreSQL `release_exact` | DENIED by SQL privilege |
| Gateway entry | Real PG restricted admission committed, then real Neo4j query starts |
| Neo4j metadata binding | Server reports exact gateway-owned IDs, witness separately verifies PG token/ref |
| Spoofed PostgreSQL ref | Witness returns `pg-binding-denied` |
| Wrong release token | Witness returns `wrong-token-or-unobserved` |
| Early release while Neo4j tx active | DENIED |
| Disposable PG primary paused while query active | Successor admission failed closed; original reservation still present after unpause |
| Worker killed and gateway SIGKILL during Bolt blackout | Real Neo4j tx remains visible; PG reservation still occupied |
| Successor attempt during uncertain physical state | DENIED at cap=1 |
| Close retained Bolt transport | Two consecutive server-side absence observations obtained |
| Witness receipt storage failure (`/dev/full`) | `custody-unavailable`; PostgreSQL slot remains occupied |
| Restored custody | Redacted Ed25519 receipt fsynced before verifier SQL release |
| Detached signature and tampering | Valid receipt VERIFIED; tampered receipt REJECTED |
| Subsequent admission | Succeeds only after verifier released exact reservation |
| Replay release | REJECTED; observer binding consumed |
| Gateway audit chain after kill | 2 local events (admission/entry intent); **no fabricated closure event** |

**Post-run cleanup:** no remaining `assistx-3party-*` containers or networks were found; disposable PG and graph were removed. The test-specific temporary audit/custody files are deleted with their temporary directory (research report captures predicates; this is NOT durable retained production evidence).

**Offline evidence:** 41 pre-existing graph-entry negatives plus 6 three-party static privilege/opt-in safeguards: **47/47 focused local tests PASS**. New focused GitHub workflow is offline-only and runs no Docker. Exact-head hosted verification should be checked separately; do not conflate physical host test with GitHub Actions.

## Explicit limitations and threat-model consequences

1. **The host bypass still exists.** Worker container `--network none` proves isolation for *that container*, not every fleet host, service/sidecar, host-networked agent, proxy, alternate Bolt port, or Tailscale address. Research UNIX socket uses `0666` permissions and would require authenticated ingress and stronger OS isolation before real use.
2. **Graph metadata is not a Neo4j server-issued authorization permit.** The authorized gateway chose the identifiers, which Neo4j later reported. Another privileged graph actor can mimic them. PG `verify_binding` narrows reservation association, but only exclusive graph access + server-enforced privilege can prevent a bypass.
3. **PostgreSQL remains one primary; term=1 is a fixture placeholder.** A paused primary denied admission; no safe takeover/quorum election was attempted. Copied/split authority can still overadmit without an independently governed monotonic fencing term and stale-effect rejection.
4. **Witness custody is local and mutable.** The file is fsynced before release, but not externally anchored, independently immutable, key-escrowed, replay-ledgered, or durable beyond fixture cleanup. The DB SQL release function relies on verifier-role custody and does **not** independently validate the cryptographic receipt.
5. **Uncertain transaction reconciliation remains local.** Two absent `SHOW TRANSACTIONS` snapshots on the same reachable Neo4j instance are not cluster-wide proof under partition, restart, generation reuse or failover.
6. **Crash leaves incomplete gateway journal.** A SIGKILL cannot append an uncertainty event. PostgreSQL reservation retention is correct, but a real production reconciler must discover and quarantine orphaned intents. A gateway that dies before reaching Bolt would require independent no-query proof before release.
7. **Performance and production identity gates remain open.** One true admitted expensive query at cap=1; no authenticated 1/3/5/10 multiworker real API load, rate fairness, trusted reverse-proxy identities, p95/p99, user data/PII policy, operational rollback or on-call approvals.
8. **The fixture host owns Docker control and bootstrap credentials.** Credential custody separation is by process arguments, not separate OS principals/nodes, independently administered trust domains, or HSM/KMS.

## Next minimal research gates — keep #148 OPEN

**A. Server-enforced network/role entry:** prove from every relevant fleet/host-network position that workers cannot reach Bolt or authenticate to any allowed read role; only a dedicated gateway identity may submit queries. Test a compromised/duplicated metadata actor.

**B. Independently durable witness authority:** append-only external custody with keyed signatures/checkpoints and verifier grant auditing; no SQL release if append/fsync/signature verification/reconciliation fails. Ensure DB release is authorized by independently validated receipts, not solely possession of a verifier credential.

**C. Generation-aware uncertainty reconciliation:** pin Neo4j instance/cluster identity + generation, handle partition/restart/epoch rollback, and preserve capacity through gateway death, worker death, gateway-before-Bolt death, and journal corruption.

**D. Controlled monotonic fencing authority:** independently governed term & quorum ownership, fail-closed takeover and stale-effect rejection, copied-primary negative cases, and uncertain in-flight reconciliation before any active failover.

**E. End-to-end operational gate:** authenticated real 1/3/5/10 clients with trace index, budgeted graph hits, p95/p99, NAT fairness, 429/503 contract, accessibility, rollback and change approval.

**Decision:** Continue in stacked research drafts. Do not mark #148 or #149 complete, activate production query admission, change current graph credentials, or deploy failover.
