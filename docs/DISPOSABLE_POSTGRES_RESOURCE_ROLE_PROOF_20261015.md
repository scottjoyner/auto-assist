# PostgreSQL 17 resource-role separation — disposable physical experiment

**Date:** October 15, 2026. **Host:** x1-370.
**Status:** RESEARCH-ONLY / NO-GO; not production authorization.

## Isolation and reproduction

A cached postgres:17-alpine image was available: no package/image download or
modification to any running service. The experiment uses a new unique container,
explicit opt-in, --pull never, --network none, no host published ports, no
host mounts or real data volumes, a read-only root filesystem, --cap-drop ALL,
no-new-privileges, strict resource limits, and disposable tmpfs PGDATA, socket
and temporary paths. It listens on loopback *inside* the container only. EXIT,
INT and TERM traps remove the container. The physical execution confirmed
network=none, readonly=true, mounts=0 and exposed ports={} and subsequently
no remaining test containers.

Random, synthetic, in-memory-only passwords are generated per run. SCRAM-SHA-256
host authentication is enforced during initdb with POSTGRES_INITDB_ARGS.
The first run with only POSTGRES_HOST_AUTH_METHOD failed closed because a wrong
password incorrectly authenticated due to earlier permissive initialization
rules. That failed run removed its container. The corrected initialization
denied the wrong password before testing SQL permissions.

**Run only with explicit synthetic research opt-in:**

    ALLOW_DISPOSABLE_PG_LAB=YES_SYNTHETIC_ONLY bash scripts/probe-disposable-postgres-acl.sh

Default invocation is BLOCKED before launching Docker. The host image must be
cached. CI never auto-runs the physical container. Offline static guard tests:

    python3 -m pytest -q tests/test_disposable_postgres_acl_contract.py

## Physical role and COMMIT findings

Two NOLOGIN owners (resource and high-water) and four restricted, distinct
SCRAM-authenticated LOGIN principals (operator, signer, verifier, observer) are
provisioned. All four have NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT.

The resource effect and receipt are both controlled by a fixed-search-path,
SECURITY DEFINER function. PUBLIC EXECUTE is explicitly revoked. Only the
operator may execute this function; only the observer may SELECT the effect
and receipt. The signer/verifier have no effect/receipt DML authority.
Neither operator nor signer nor observer can update the high-water table.

Actual PostgreSQL 17.11 outcomes:

- **DENIED** incorrect signer password and verified signer/operator login.
- **DENIED** signer effect UPDATE and receipt INSERT, verifier effect UPDATE,
  operator direct UPDATE/DELETE, signer/verifier direct commit-function EXECUTE.
- **DENIED** observer/operator/signer high-water UPDATE.
- **PASS** authorized operator resource commit, observer fresh-connection
  readback of actual commit sequence and matching receipt, stale sequence
  rejected through controlled function.
- **REPRODUCED** two transactions both reported as ACK_NOT_OBSERVED:
  one ROLLBACK (resource remains prior state), one actual COMMIT (resource
  advanced). Independent readback, not the ACK, distinguishes the outcome.

## Required NO-GO negative results

The same PostgreSQL superuser retains authority over both resource and
high-water regardless of role ACLs. The temporary superuser was able to:

1. Restore the high-water marker from sequence 3 to 1, reopening replay.
2. Fabricate an effect at sequence 4 and matching receipt via privileged SQL,
   bypassing the authorized commit function. The observer then saw matching
   rows; matching rows do **not** prove actual resource authority.

All experiments were isolated in the temporary container. No production
Postgres, Neo4j, graph, inference runtime, deployment, credential, network,
or phone was contacted or changed. The same OS/Docker administrator and
database superuser own both sides of this test, so it does **not** demonstrate
independently governed credentials, protected source signatures, resource
attestation, a restore-proof external monotonic anchor or finalization
fencing.

## Smallest safe subsequent acceptance proof

Use separately governed **disposable** resource and monotonic high-water
authorities, with demonstrably independent DBA/signing/verifier custody.
Require deny-authoritative direct SQL grants, resource-owned commit readback,
term fencing, restore and replay adversarial cases, and uncertain-COMMIT
readback without automatic retry. Do not substitute a NOLOGIN table owner
inside one superuser-controlled database for independent governance.

Related drafts Auto-Assist #368–#374; open security NO-GOs: Auto-Assist #299/#156;
Kipnerter-API cryptographic #46, production graph #31, hosted CI #45,
Auto-Router native owner receipts, iOS device/App Store gates. **NO MERGE,
NO DEPLOY, NO PRODUCTION EFFECTS.**
