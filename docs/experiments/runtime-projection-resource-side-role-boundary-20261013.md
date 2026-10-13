# P0: independent Neo4j approval resource boundary (draft research)

**Date:** October 13, 2026. **Host:** x1-370, via Fleet Commander.
**Target stack:** stacked after draft auto-assist PR #305; no merge, no live
Docker or Neo4j role/user/topology changes, no graph admission or failover.

## Physical source-of-truth finding

A bounded read-only inspection of running `assistx-api` and
`assistx-worker` confirmed **both use `NEO4J_USER=neo4j`**, with a
password configured. The running API's authenticated session returned
`admin` in `SHOW CURRENT USER` roles. No usernames (other than the
literal built-in account), passwords, URI values, tokens, graph payloads
or model keys were printed. The worker's own authenticated Neo4j role
inventory was not queried; its built-in username is nevertheless
overprivileged and it has proven direct TCP reachability in PR #294.

A second **read-only** live Neo4j `SHOW CONSTRAINTS` inspection found
**53 constraints total**, but **neither** the required unique
`FleetProjectionState.name` nor unique
`RuntimeProjectionApprovalReceipt.approval_id` constraint exists.
No constraint was installed; current graph state stayed unchanged.
PR #305's verifier already explicitly refuses real `--apply` if either
constraint is absent.

## Disposable physical RBAC counterexample

`tests/probe_runtime_projection_broker_rbac_physical.py` starts a
cached `neo4j:5.26-enterprise` container on a random internal Docker
network with no published ports, no host mounts and only anonymous
volumes, deleted via `docker rm -fv`. It uses fresh random test-only
credentials. It does not read live `NEO4J_*` secrets or connect to the
running production graph. The only graph writes are **synthetic fixture**
records in this disposable database.

The fixture creates three database users/roles, including two ordinary
application callers and one distinct broker writer:
- API and worker roles can READ the canonical generation but Neo4j
  **denies both** `SET canonical.expires_at_ts` and
  `CREATE RuntimeProjectionApprovalReceipt`.
- The broker role can update the canonical expiry and create a
  receipt; thus a dedicated broker can receive least-privilege write
  grants while general-purpose clients retain read-only privileges.
- **Compromised-broker negative:** without signature verification
  *at the boundary*, the broker can insert an arbitrary unsigned
  `source_receipt_sha256` value. A trusted account alone is not
  an independent Ed25519 verifier.
- Database UNIQUENESS on the receipt ID must reject a duplicate
  approval ID; UNIQUENESS on canonical name must reject duplicate
  canonical rows. The broker can still **roll the generation
  backward** if granted `SET PROPERTY {generation}`. Unique indexes
  do not enforce high-water values, signature proofs or generation
  monotonicity.

Do not interpret success as an authorization to change production
Neo4j roles, credentials, networks, source timestamps or approvals.

## Acceptance dimensions, intentionally independent

1. **B01 exclusion:** migrate all authorized direct graph consumers,
   rotate API/worker credentials away from the builtin `neo4j` admin,
   remove graph-write permissions and direct Bolt bypass. Prove
   both the authenticated denial and lack of alternate graph network
   paths before claiming exclusive broker authority. Current state NO-GO.
2. **Pinned cryptographic authority:** PR #305 verifies two independently
   trusted Ed25519 source and reviewer signatures and the exact manifest;
   do not let in-band data choose keys. The broker must be the sole
   principal able to write canonical/receipt labels. A directly
   credentialed admin can bypass application verification; separate
   credential custody, ACLs and independent audit are mandatory.
3. **Transaction/replay:** install and verify unique canonical-name and
   receipt-ID constraints after an explicit duplicate/capacity audit and
   maintenance approval. CAS over expected generation and a monotonic
   high-water/fencing check must execute atomically, including denial
   of retry, stale generation and conflicting receipts. Mere grants to
   `SET generation` do not guarantee this.
4. **Source age:** keep PR #307 signed original observation windows
   and the separate authenticated physical model/artifact/loaded-state,
   path/capacity canary evidence. Do not refresh old timestamps.
5. **Production access and rollout:** shadow on separate staging
   credentials first, exact-head CI and independent reviewer, then
   separate approved maintenance window and rollback. No automatic
   reenable of the P0 host renewal scripts or systemd timers.

**Current disposition:** `DRAFT_ONLY_RESOURCE_ROLE_BOUNDARY_PROVEN_BUT_CRYPTO_HIGH_WATER_AND_B01_HOLD`.
Even if the entire disposable fixture passes, this is *not* proof of
independently verified signatures or system-wide resource-side
authorization. No production apply, privilege mutation, deployment,
auto-router promotion, reservation release or automatic failover.


## Executed October 13 physical receipt

- x1-370, cached `neo4j:5.26-enterprise`, standalone random credentials,
  internal Docker network, **no published ports** or host binds.
- Physical runner **PASS, exit 0, 26.15 seconds** (second strengthened run).
- Ordinary synthetic API/worker roles: **2/2 canonical expiry writes
  denied** and **2/2 approval-receipt creates denied**; scoped canonical
  generation READ available to both.
- Synthetic broker role: deliberately could write arbitrary unsigned
  `source_receipt_sha256` and set generation backward from 643 to 642
  (**security counterexamples reproduced**).
- Disposable database uniqueness constraints denied duplicate canonical
  name and duplicate receipt approval_id. **Production has neither**.
- Combined offline PR #305 signature/role suite: **43/43 pass**; entire
  physical container and network were cleaned; production services healthy.
- Production `assistx-api` and `assistx-worker` presently use the
  built-in Neo4j account; API session was independently confirmed
  member of the `admin` role (only boolean/role count emitted).
- Read-only production `SHOW CONSTRAINTS` returned 53 existing constraints,
  with **neither** `FleetProjectionState.name` nor
  `RuntimeProjectionApprovalReceipt.approval_id` unique.
- These checks do not authorize changes to production grant policies,
  credentials, graph topology, constraint schema, source roots or routing.

This is the smallest tested graph-level privilege split, **not** a
resource-side Ed25519 signature check or irreversible generation high-water
enforcement. The latter must be designed so even a compromised broker
credential cannot present arbitrary unsigned approvals or decrement
the canonical generation.
