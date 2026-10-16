# Real PostgreSQL 17 effect-role authorization: restricted caller is not resource custodian

**October 16, 2026 | Fleet Commander x1-370 | DRAFT research, CRITICAL NO-GO**

## Reason for this experiment

Auto-Assist draft #398 demonstrated with actual SQLite + self-SIGKILL that
owner/term/epoch checks can be made in the same local transaction as a
synthetic effect. It also proved two bypasses: privileged direct SQL can
forge an effect, and privileged SQL can rewind revoked owner terms. This
experiment physically tests the **least-privileged caller** boundary in
a disposable real PostgreSQL 17 cluster, without representing a shared
database superuser as independently governed resource authority.

### Environment and safe execution

The opt-in-only `scripts/probe-disposable-pg-effect-authority.sh`
requires `ALLOW_DISPOSABLE_PG_EFFECT_AUTHORITY=YES_SYNTHETIC_ONLY`,
otherwise denies before touching Docker. It uses an already-cached
`postgres:17-alpine` image and one short-lived container with:
`--network none`, no ports or host bind mounts, read-only root,
`--cap-drop ALL`, no-new-privileges, bounded CPU/RAM/PIDs, tmpfs
PostgreSQL state and UNIX socket, SCRAM-SHA-256 host auth and freshly
generated **synthetic** role passwords. No real credentials are
printed, stored in Git or reused from the fleet. EXIT/INT/TERM cleanup
removes the container; observed zero leftovers.

Roles are created by
`scripts/disposable-pg-effect-time-authority.sql`:

- `fx_owner`: NOLOGIN schema/table/function owner.
- `fx_executor`: only EXECUTE on narrow `fx.execute_effect(...)`;
  **no direct INSERT** on the applied-effect table, UPDATE on owner,
  or access to the revocation procedure.
- `fx_revoker`: only EXECUTE on monotonic `fx.rotate(...)`, no
  direct applied-table INSERT and no execution permission.
- `fx_observer`/`fx_reviewer`: SELECT only; cannot execute or
  rotate authority.

Functions are `SECURITY DEFINER SET search_path=pg_catalog`, with
PUBLIC EXECUTE revoked. The effect procedure locks the sole resource
authority record `FOR UPDATE`, compares exact term/epoch/owner/revoked
state, and records an effect with unique sequence and nonce. The
revocation procedure uses expected-value CAS and a strictly monotonic
(term, epoch) pair. None of these facts authenticate a real hardware,
model, network or business effect.

## Physically observed narrow successes

The real ephemeral PostgreSQL 17 server rejected:

- Executor direct SQL INSERT into applied or UPDATE to revoked authority;
- Revoker direct INSERT or effect-procedure execution;
- Observer/reviewer execution or revocation attempts;
- Executor direct revocation;
- Duplicate synthetic effect sequence/nonce;
- Use of an old term after revocation, use of a revoked owner,
  and stale/nonmonotonic authority rotation.

After valid monotonic owner re-grant, a new owner can execute. Two
different authenticated executor connections raced concurrently to
apply **the same synthetic sequence** with distinct nonces; exactly
one committed and one was denied. Independent restricted observer
readback confirmed one applied row. This proves ordinary SQL
role isolation and transaction-level serialization only.

## Critical NO-GOs *physically reproduced*

1. **DB superuser bypass:** the PostgreSQL superuser directly INSERTs
   a fabricated applied-effect row without calling the executor
   function. Restricted observers read it as an ordinary applied row.
   A database row is not proof of authorized COMMIT or physical effect.
2. **DB superuser rewinds revocation:** a direct table UPDATE restores
   the earlier owner/term/epoch even after a monotonic restricted
   revocation. The previously valid old owner can execute again.
   Restricted-role CAS does not fence DBA/host administrators.
3. **Even restricted executor can forge a logical claim:** an authorized
   executor submits a well-shaped arbitrary digest and sequence
   while matching current term/epoch/owner. The SQL function accepts
   it because there is no independently authenticated committed
   resource-effect receipt or immutable attestation root.
4. **Common Docker administrator:** even with a NOLOGIN table owner
   and role-separated SCRAM credentials, the local host Docker
   administrator can `docker exec` as the cluster superuser. Earlier
   #377/#378 independently showed the same fleet operator can reach
   Docker on xwing/destroyer; moving containers would not prove
   separate host/backup/key control.
5. **No cross-system authority:** the demonstration is a single toy
   Postgres cluster. It does not share an atomic transaction with
   source+anchor+signed outbox from #397/#398, and does not provide
   cryptographic term grant, independent signer, hardware effect or
   restore-resistant high-water.
6. **No production readiness evidence:** PostgreSQL role tests were
   run against short-lived synthetic credentials and disposable
   database only. They neither validate actual resource permissions,
   malicious DBA denial, external effects, key rotation nor split-brain
   revocation.

The positive physical denials and the positive attack reproductions
are deliberately treated as **NO-GO** evidence, not a release gate.

## Reproduce — explicit opt-in, no production changes

Source-only tests (never run Docker):

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
      tests/test_disposable_pg_effect_authority_contract.py

A deliberate disposable physical PostgreSQL run (requires local Docker
access and a cached postgres:17-alpine image):

    ALLOW_DISPOSABLE_PG_EFFECT_AUTHORITY=YES_SYNTHETIC_ONLY \
      bash scripts/probe-disposable-pg-effect-authority.sh

The script creates exactly one ephemeral container under label
`research.only=disposable-fx-authority` and always removes it on exit.
Do not copy the lab roles/functions to production: their structure
is intentionally **insufficient for authentic resource effects**.

## Smallest real acceptance step

Provision or designate an authority *outside the source/anchor and
witness DBAs' shared OS/Docker, backup and emergency admin domain*.
Prove the resource's actual effect is committed and attested only by
that authority, with independently pinned verifier roots, exact
term/owner/epoch/fencing, revocation that survives full host/DB restore,
and negative tests where source/anchor DBA and two signing members
collude. Verify uncertain COMMIT by independently governed readback
and never automatic retry.

Preserve existing Auto-Assist #156/#299, Context crypto #46 and
production graph #31, hosted CI #45, Auto-Router owner effects, model
admission and real iOS/TestFlight physical acceptance **CRITICAL NO-GO**.
No branch merges, service deployment, production graph, credential,
network, TPM, model, failover or device actions authorized.
