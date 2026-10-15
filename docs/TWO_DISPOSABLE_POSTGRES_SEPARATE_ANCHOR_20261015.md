# Two isolated PostgreSQL clusters: resource and external high-water

**October 15, 2026 | Fleet Commander x1-370 | Research-only | Production NO-GO.**

## Hypothesis and outcome

Split a resource effect/receipt and an anti-replay high-water marker across
two fresh, network-isolated PostgreSQL 17 instances with distinct randomly
generated temporary credentials and distinct restricted SQL roles.

**The physical experiment confirms cross-cluster authentication/role denial
and detects source-only restoration when the external marker survives.**
It does not establish independent administration: the same Docker host
administrator can enter both database clusters as superuser and rewrite
both sides, defeating anti-rollback. A resource DBA can fabricate a matching
commit receipt without running the authorized commit procedure.

## Reproduce only with explicit local synthetic opt-in

    ALLOW_TWO_DISPOSABLE_PG_LABS=YES_SYNTHETIC_ONLY \
      bash scripts/probe-two-disposable-postgres-clusters.sh

The default invocation refuses **before any Docker call**. The runner uses
only the already cached postgres:17-alpine image and refuses image pulls,
existing container name collisions or inadequate isolation. Both containers
have --network none, no published ports, no host data volumes, read-only
roots, dropped capabilities, no-new-privileges, bounded CPU/memory/processes
and three tmpfs-only directories. The entrypoint binds only inside its own
isolated network namespace. A shell trap destroys both instances. Physical
postrun inspection found **zero remaining experimental containers**.

SCRAM authentication is enforced at initdb using POSTGRES_INITDB_ARGS to
avoid the permissive host-init issue found in #375. All temporary secrets
are randomly generated for the one run, held only in memory, never printed,
persisted, or used for production.

No Docker is used by static contract tests:

    python3 -m pytest -q tests/test_two_disposable_postgres_clusters_contract.py

## Actual physical receipts

- DENIED: resource operator/observer credentials on the anchor and anchor
  writer/reader credentials on the resource. Resource signer has no role on
  the anchor. Resource superuser password cannot authenticate to the anchor.
- DENIED: signer effect UPDATE, anchor reader or writer direct high-water
  UPDATE, anchor reader calling the privileged advance procedure.
- PASS: resource operator makes genuine COMMIT to sequence 2 and separate
  read-only resource observer verifies the matching receipt. Anchor marker
  in the *other PostgreSQL cluster* remains at sequence 1.
- PASS: anchor writer advances its marker from 1 to 2 through the explicitly
  granted bounded monotonic SECURITY DEFINER procedure; stale and
  non-monotonic anchor advances are denied.
- DENIED: resource-side privileged rollback from sequence 2 to 1 leaves the
  external high-water at 2. Resource state is visibly stale relative to
  the independent database's marker.
- NO-GO: shared Docker host administrator executes as anchor superuser and
  rewrites marker 2 back to 1 while resource state remains sequence 2.
  The replay window is reopened despite separate passwords and instances.
- NO-GO: resource superuser fabricates sequence 3 effect and a matching
  commit receipt by direct SQL, bypassing the controlled resource procedure.

The legacy toy high-water table included in the stacked #375 resource SQL
exists only for fixture compatibility; it is **not** the authoritative
anti-replay marker in this physical two-cluster experiment.

## Critical limitations and next test

Two database instances are not two independent trust/governance domains when
one Docker/host administrator can inspect, destroy, restore and rewrite both.
This work does NOT establish independent key custody, restore-resistant
high-water, trusted resource-owned COMMIT evidence, fenced term authority,
cross-resource atomic finalization, revocation or uncertain-COMMIT safety.

The next experiment must separate administrative control planes and secret
custody with a separately controlled monotonic service, and test privileged
DBA collusion, resource rollback, signer forgeries and effect-time TOCTOU
against independently authorized disposable resources.

Related existing HOLDs: Auto-Assist #156/#299, Context #46/#31/#45,
Auto-Router native receipts, and physical iOS release. No merge, deploy,
production graph mutation, network changes, credential rotation or model
promotion is authorized.
