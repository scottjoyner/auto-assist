# Canonical ServiceEndpoint/ModelEndpoint identity — unwired acceptance candidate

Security dependencies: [P0 identity collision #322](https://github.com/scottjoyner/auto-assist/issues/322), [schema/runtime separation #318](https://github.com/scottjoyner/auto-assist/issues/318), [read-only schema audit PR #321](https://github.com/scottjoyner/auto-assist/pull/321).

**Research code only. NOT connected to API, worker, model discovery or real graph writers. NO production deployment, graph repair, DDL, credential rotation or release authorized.**

## The defect this candidate addresses

Deployed `swarm_core.py` has two writer keys: `ServiceEndpoint.endpoint_id` versus `ModelEndpoint.model_endpoint_id`, even though model endpoints also carry the `ServiceEndpoint` label and property. In the real x1 graph, one service-first/model-second collision involves two different nodes and prevents the declared ServiceEndpoint uniqueness constraint. `CREATE CONSTRAINT` would fail while duplicates exist; after correcting synthetic duplicates and enforcing uniqueness, the *unchanged* legacy model writer fails its service-first path.

Changing only the model writer or blindly adding the constraint is insufficient. Both types of registration must share an identity rule; existing data must be separately reconciled with relationship preservation.

## Proposed conservative identity contract

This PR adds `src/assistx/endpoint_identity_candidate.py` but **does not import it from any running route or existing writer**. The candidate provides two separately testable operations, `candidate_register_service_endpoint` and `candidate_register_model_endpoint`, with these principles:

- Canonical key: **`ServiceEndpoint.endpoint_id`**. `ModelEndpoint.model_endpoint_id` must equal it when the model subtype is present.
- Database MUST already enforce a node-property **UNIQUENESS** constraint on `:ServiceEndpoint(endpoint_id)`. The candidate refuses all writes when this is absent, which is the **current production state**.
- A model endpoint is a deliberate ServiceEndpoint subtype. A service-first record can gain `ModelEndpoint` only when its existing `service_type` is `model_endpoint` or unset. Ordinary/generic services are **never silently retyped**.
- Shared endpoint IDs cannot cross SwarmNode owners. Every existing owner relationship and recorded node owner must be consistent. Duplicated nodes, orphaned model identities or conflicting model IDs cause an explicit **HOLD**, not selection or auto-merge.
- Both writers use a **managed write transaction** with preflight, write-lock acquisition on the constrained canonical node, and **post-lock revalidation** of ownership and type. This second read is essential: a different writer can win the race between an initial `MATCH` and `MERGE`. Failed checks roll back writes.
- Only explicitly permitted non-identity metadata fields can be updated. `EXPOSES` and `SERVES` are added idempotently; the candidate never prunes model relationships or deletes graph nodes.
- Neither writer changes application authentication, graph roles, schema, Tailnet policy, or the existing runtime's operational code.

### Known limitations before any integration

The candidate is **not** a drop-in replacement for current event ingestion. Existing writers still accept additional metadata fields, source-specific payloads and optional `models` lists that can remove stale `SERVES` relationships. The prototype intentionally excludes destructive pruning, broad property updates and production event custody. End-to-end discovery/route rendering, telemetry, provenance and delete behavior require separate acceptance.

**Old and new writers must not run against the same endpoint identities concurrently.** The old writer can still change a type or identity field without honoring these locks, and production currently lacks the required uniqueness constraint. A new caller merely importing this module does not fix the live graph.

## Reproducible physical acceptance (never production)

The opt-in script `scripts/endpoint_identity_candidate_disposable.py` creates exactly one disposable `neo4j:5.26-enterprise` container on a `docker network create --internal` bridge with no `-p`, no host mounts, and a random temporary admin password, then unconditionally removes its container and network.

The script refuses all Docker activity by default:

```sh
python3 scripts/endpoint_identity_candidate_disposable.py
# HOLD_EXPLICIT_DISPOSABLE_OPT_IN_REQUIRED, exit 2
```

A qualified x1 operator can explicitly authorize the ephemeral experiment:

```sh
python3 scripts/endpoint_identity_candidate_disposable.py --run-disposable
```

Tests use **synthetic identifiers only**. The script validates Docker network and mount isolation before connecting, never uses the production Neo4j URI, and outputs boolean scenario statuses rather than IDs, node attributes, credentials, or graph data. An affirmative result is `PASS_DISPOSABLE_UNWIRED_CANDIDATE_ONLY` and **cannot promote the release**.

### October 11, 2026 witness

The exact packaged harness was physically executed on x1 with Neo4j 5.26 Enterprise. Offline candidate contract tests: **41 passed / 47 subtests**. Disposable physical checks: **9 of 9 PASS**, including:

| Synthetic scenario | Evidence |
|---|---|
| No uniqueness constraint | Both candidate paths require schema enforcement; model path explicitly denied |
| Model first → service second | One canonical dual-labeled endpoint, stable EXPOSES and SERVES |
| Service first → model second | Compatible model-typed service promoted, no duplicate or lost edges |
| Repeated model registration | Idempotent model and relationship identity |
| Generic service → model attempt | HOLD; no silent retype |
| Foreign SwarmNode owner | HOLD; no cross-owner link |
| Same-owner concurrent model writers | Exactly one canonical node and one EXPOSES |
| **Different-owner concurrent model writers** | Synchronized pre-lock reads; one success, one HOLD after write lock |
| Isolation and cleanup | Internal-only bridge, no published ports or host mounts, test graph/container/network removed |

These are synthetic witnesses, not production data acceptance or proof that the entire distributed fleet uses the proposed transaction path.

## Release sequence — not executed

1. Confirm schema/data owner and decide canonical identity semantics, including whether a service/model namespace collision indicates one endpoint or two distinct services. See #322. Document relationship ownership of `EXPOSES` and `SERVES`.
2. Add a dedicated migration tool in a separately reviewed PR that preserves immutable provenance and all graph relationships. Test on synthetic reproductions **with both creation orders** and on isolated copies of live *structure*, not by rewriting current records.
3. Quiesce and fence **both** legacy endpoint writers; verify the complete set of writers and readers (including API, worker, scheduled jobs, Fleet clients) and take owner-approved reversible custody snapshots.
4. Reconcile the existing live identity collision under owner approval, then validate uniqueness index/constraint creation; independently require the schema audit and collision audit from PR #321 to report zero drift.
5. Migrate both writers together to the reviewed canonical implementation. Extend the prototype to preserve authenticated event provenance, all validated input fields, model pruning semantics and discovery consumers.
6. Independently complete database-scoped replacement accounts, API cold start and PhoneLog authorized ingestion, **14 historical credential custodians**, complete graph caller inventory and dual-stack Tailnet ingress closure. Full CI and real device/recovery acceptance remain independent.
7. Restore from the approved rollback if ANY relationship, provenance, permission, fleet-model visibility or cross-owner invariant fails.

**Current decision: NO-GO.** The only graph mutation performed for this PR's validation was on automatically removed disposable data. Nothing was written to production Neo4j, runtime configuration, Tailnet rules, accounts or NAS.
