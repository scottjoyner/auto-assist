# Disposable resource-bound HMAC receipt before anchor CAS

**October 15, 2026 | Fleet Commander x1-370 | Research-only / NO-GO.**

## Change in proof strength

Earlier physical experiments proved that PostgreSQL anchor CAS serializes
concurrent advances but that the **authorized anchor writer can advance
without a resource COMMIT**. This experiment removes that permissive path
for normal restricted test roles in a *fresh, disposable, two-cluster lab*.

After installing base role/schema fixtures, this lab:

- Revokes `hw_writer` from the unverified `hw.advance` entrypoint and
  revokes `lab_operator` from the unsigned `lab.apply_effect` entrypoint.
- Adds an owner-only **synthetic HMAC-SHA256 key** to each ephemeral DB.
  The *same* secret is installed in both clusters for this demonstration.
  Restricted operator and anchor-writer roles cannot read either key.
- Adds `lab.commit_with_mac` which updates resource effect and inserts a
  keyed, domain-separated receipt **in the same PostgreSQL transaction**.
  The receipt binds fixed resource identity, term, sequence, generation,
  checksum, revision digest and nonce.
- Adds `hw.advance_verified_receipt` which verifies that proof **before**
  an adjacent-position, term-qualified atomic compare-and-swap update.
  There is no way for ordinary anchor writer to select a different
  `p_new_seq` without invalidating the MAC or violating adjacent CAS.
- Maintains operator/observer/signature-reader distinctions, explicit
  `SECURITY DEFINER SET search_path=pg_catalog`, and revokes implicit
  PUBLIC function execution.

The physical experiment demonstrates (against real PostgreSQL 17):

| Property | Restricted-role test |
|---|---|
| Legacy unverified advance or unsigned resource COMMIT | DENIED |
| Direct operator/anchor-writer secret-key SELECT | DENIED |
| Anchor advance with no COMMIT/guessed proof | DENIED |
| Tampered generation or jumping over an uncommitted sequence | DENIED |
| Controlled operator COMMIT then anchor receipt verification | PASS |
| Same valid receipt reused against stale CAS | DENIED |

## Safety and reproducibility

Run only explicitly with:
```sh
ALLOW_DISPOSABLE_RECEIPT_LAB=YES_SYNTHETIC_ONLY \
  bash scripts/probe-disposable-commit-bound-receipts.sh
```

Default invocation is **BLOCKED** before invoking Docker. The runner
starts two uniquely named, ephemeral PostgreSQL containers using an
already-cached image: `--pull never`, `--network none`, no host ports,
no host volumes, read-only roots, capability drop, no-new-privileges,
resource limits and tmpfs-only data/socket/temp. Separate synthetic
SCRAM role credentials and a synthetic HMAC key are generated per run.
No production secret is accessed. The HMAC secret is intentionally
shared by both containers **in this lab only**, and is not printed or
committed. A shell exit trap removes both containers; postrun inspection
confirmed no lab containers remained. All test checks are read-only or
write only to ephemeral lab databases.

Offline, no-Docker tests:
```sh
python3 -m pytest -q tests/test_disposable_mac_receipt_contract.py
```

## What this absolutely does NOT establish

**Critical NO-GO: Shared HMAC key and host control.** This is symmetric
MAC authentication, **not independent resource-owned signing custody**.
The same test harness, Docker administrator and PostgreSQL superusers
control the key, data and both machines. A resource-superuser or
anchor-superuser can extract or replace the secret; an administrator
can fabricate a plausible receipt. A HMAC alone does not establish
which process created a receipt or whether a downstream *real-world
resource effect* actually occurred.

**Critical NO-GO: Privileged restore and replay.** A legitimate MAC
receipt can be replayed after the anchor's checkpoint is restored.
An external restore-resistant high-water owner is still required.
A valid receipt copied from the resource DB may also outlive a later
resource rollback; anchor verification here does not perform a trusted
fresh resource-side readback at finalization time.

**Critical NO-GO: Cross-database causality.** Resource COMMIT and anchor
CAS are separate PostgreSQL transactions. A lost ACK between them remains
uncertain, no automatic retry is authorized, and simultaneous resource
writes/effects are not fenced by this proof. Neither a shared secret nor
a green CI job can authorize promotion into the production graph.

## Required next real authority

Before any production path: independently governed asymmetric source
signing keys (never exported to the anchor), resource-owned commit
attestation and protected trust-root pin, external monotonic anchor
whose backup/restoration cannot be controlled by source or fleet
administrators, independently verified fresh readback and effect-time
fencing, and replay/high-water conflict tests under real negative
permissions. Any authorized anchor-writer forgery without actual
source effect must be denied. Keep all NO-MERGE/NO-DEPLOY/NO-GRAPH/
NO-CREDENTIAL/NO-MODEL/NO-IOS constraints intact.
