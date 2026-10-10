# AssistX #148 — verified HTTP idempotency at research graph admission

**October 10, 2026 · Research-only, stacked on draft PR #273 · Production admission/failover NO-GO**

## Decision and actual implementation

This slice closes the narrow *research integration* gap between a verified HTTP caller's stable logical request and a quorum-persisted graph-admission operation. Earlier PR #273 added persistent exact-operation replay tombstones, payload conflicts and quorum reconciliation, but `ProtectedGraphEntry.execute()` still generated `uuid4()` for every call. Thus **an ordinary HTTP retry could pass the earlier correct Raft replay ledger with a fresh operation ID**. The new code adds `execute_authenticated` and passes a derived operation ID + request digest to `admit_stable` without altering the legacy live route.

## Source and end-to-end sequence

- `tests/trace_graph_stable_identity_research.py`: `derive_operation_key(verified_subject, client_request_id)` deterministically hashes a *previously verified* principal + client-issued idempotency key; the separate digest hashes reviewed plan ID + canonical parameters. A reused ID with different plan/parameters yields a collision-resistant payload conflict, **not** a new operation.
- `tests/trace_graph_entry_guarded.py`: `execute_authenticated(plan_id, parameters, verified_subject, client_request_id)` preserves allowlisted Cypher and strict parameter serialization, then invokes `admit_stable`. Prior `execute` remains a legacy per-attempt interface (new UUID), not an end-to-end retry-safe path.
- `tests/trace_etcd_graph_admission_research.py`: explicit quorum CAS `admit_stable` with operation ID `auth:<sha256>` and SHA-256 request digest. **No plan-name prefix in the operation ID**, since prefixing would let a changed plan with a reused logical request ID bypass the duplicate key.
- `tests/trace_etcd_policy_writer_research.py`: the Unix policy writer now offers bounded `admit_stable` and read-only `reconcile` actions. The client never obtains raw etcd KV APIs or writer bearer credentials. Known safe duplicate/payload/closed denials survive IPC as typed public codes; internal/quorum failures remain masked and fail-closed.
- `tests/trace_graph_http_ingress_research.py`: an **unmounted** FastAPI HTTP endpoint requires an `Idempotency-Key`, obtains its identity from an injected verified-principal dependency, and rejects attempts to reuse one request ID for another execution. It returns **409** for safe known conflicts, **503** for uncertain/unavailable authority, **422** for invalid plans/keys, and **428** when a key is missing. A read-only `GET .../requests/{client_request_id}` consults a quorum-linearizable reconciliation source and returns only `PENDING_EXECUTION_UNCERTAIN`, `CLOSED_NO_REEXECUTION` or `ABSENT_NOT_AN_EXECUTION_PERMIT`; it does not expose a grant/tx ID or initiate graph execution.
- `tests/test_trace_graph_http_ingress_research.py`: real FastAPI TestClient verifies fixed test HTTP Basic credentials, refuses spoofed X-Verified-Subject/Tailscale-User-Login headers, concurrent retries, changed payloads, closed replay, admission-ACK loss, quorum outage, actual Unix IPC path and writer process disappearance. The test-credentials dependency is not a proposal to change production Tailnet SSO.
- `tests/probe_trace_http_raft_ingress.py` + conditional hook in `tests/probe_trace_etcd_rbac_physical.py`: manual physical path via temporary FastAPI TestClient → bounded Unix policy writer child → exact-key RBAC bearer auth → three-voter real etcd Raft CAS. The HTTP phase uses a **stub graph executor**. Earlier physical subprobe in the same full run uses a **real separate Neo4j 5.26 transaction** and detached witness, so these must not be conflated.

## Verified physical results — x1-370 + xwing + destroyer

**Final combined opt-in physical run: PASS, 52.12 seconds, exit 0**, with all flags enabled:
`ASSISTX_THREE_HOST_RAFT_RESEARCH=1 ASSISTX_RAFT_ACK_LOSS_RESEARCH=1 ASSISTX_QUORUM_NEO4J_RESEARCH=1 ASSISTX_RAFT_RBAC_RESEARCH=1 ASSISTX_HTTP_IDEMPOTENCY_RESEARCH=1`.

In the new authenticated HTTP subprobe, a fake test-authenticated client requested `approved_read` and received one real Raft reservation via Unix IPC; the second identical HTTP request returned 409, and a request with the same idempotency key but changed payload returned 409. A read-only GET reported `PENDING_EXECUTION_UNCERTAIN`. No second graph stub execution occurred. After the separate policy-writer process was terminated, the exact reservation persisted in the Raft cluster. The existing independent true Bolt/witness, leader reelection, minority direct raw Txn, RBAC reader restriction and lost-ack reconciliation controls all passed in the same disposable session.

No temporary cluster/service/container/research credentials are retained for deployment. See `research/evidence/assistx-verified-http-raft-20261010.json`.

## Security counterexamples and still-open gates

**P0 — Actual production identity path is not accepted.** The live Kipnerter model completion handler currently creates `mobile_request_id = f"kmr:{uuid.uuid4().hex}"` per call in `src/assistx/mobile_agent_routes.py`; that is *not* a stable client idempotency key. It also has a Tailscale-header trust mode. The helper `_tailnet_identity` reads `Tailscale-User-Login` when `TRUSTED_AUTH_HEADER` is configured; its safety depends on **every direct/untrusted route to this listener being blocked and only an authenticated, header-scrubbing Tailscale Serve path injecting that header**. This deployment premise has **not** been checked on every fleet node/port, so a directly exposed route would be a spoofing risk. Our synthetic HTTP Basic test does not establish that real SSO header provenance is safe.

**P0 — The research route isn't mounted and does not wrap the current mobile model/chat or agent stream endpoints.** Streaming reconnection semantics, partial tokens/results, downstream provider side effects and cancellation require an independently accepted contract. There is no promise of exactly-once model execution; the tests establish **at-most-one admission for a preserved verified request ID** inside the research prototype.

**P0 — Cannot equate query result with physical closure.** The HTTP route returns a result while Raft capacity remains occupied pending independent Neo4j closure witness. Known duplicate returns 409, not a cached response. A safe user-facing result-retrieval design needs durable answer storage keyed by verified principal, privacy/retention policy and a no-reexecute replay contract.

**P0 — Same-UID policy writer + direct Bolt bypass.** Research actor and writer still share a Unix UID on x1-370, and Neo4j does not server-validate a fencing grant. External Neo4j credential/network isolation, separate OS identities/secret custody and cluster-generation-aware independently durable closure evidence remain unproven.

**P0 — External failure classes.** Real HTTP packet ambiguity, node power loss, etcd NOSPACE/compaction/restore, copied snapshots, Neo4j restart/multi-member transaction observation, signing-key crash and external custody corruption remain unresolved.

**P1 — liveness and scale.** Current completed-operation ledger has a deliberately tiny 128-tombstone limit and fails closed once full; production requires a sharded durable replay history and explicit retention/garbage-collection proof.

### Read-only live ingress exposure inspection (x1-370)

A read-only inspection of the actual `assistx-api` Docker published port and Tailscale Serve routing showed `127.0.0.1:8000→8000/tcp` for the API, while Tailscale Serve terminates at host port `8443` and proxies `/api/v1/auth/whoami`, `/api/v1/runtime/catalog`, `/api/v1/model/chat/completions` and `/api/v1/agent/chat/completions` to the localhost API. **This is not a general-public `0.0.0.0:8000` host publication**; API container-internal `uvicorn --host 0.0.0.0` alone is not evidence of host exposure.

A **read-only** GET against `http://127.0.0.1:8000/api/v1/auth/whoami` without an identity header returned HTTP **401**. With a deliberately invented, non-allowlisted `Tailscale-User-Login: assistx-boundary-check@example.invalid` header, it returned HTTP **403**. Thus the synthetic spoof was denied, which is positive evidence of the current allowlist, but **does not prove** that a local or Docker-bridge actor cannot spoof a *real allowlisted login*. Such impersonation was deliberately **not tested** against a live account. The required next gate is proof of host/bridge isolation, proxy header stripping, and identity provenance at the API boundary. No service configuration or firewall was changed.

The raw model-router service on `0.0.0.0:8088` is a separate host listener and must be assessed independently; its existence is not proof of direct access to the mobile SSO route. The source code and topology have no production graph-admission integration yet.

## Acceptance status

- Authenticated FastAPI research endpoint under test: **PASS**.
- Real Unix policy-writer protocol with stable request digest: **PASS**.
- Read-only principal-scoped reconciliation: **PASS**.
- Disposable real three-host Raft RBAC-backed admission from HTTP: **PASS**.
- Real Neo4j Bolt and detached closure witness in separate subprobe of same run: **PASS**.
- Current production FastAPI routes and Tailscale proxy trust: **NOT ACCEPTED**.
- Production graph-effect fencing / automatic failover: **NO-GO**.

**Do not merge into production or mount the research route.** Keep #148 open until live identity and nonbypassable graph entry boundaries have independent acceptance.
