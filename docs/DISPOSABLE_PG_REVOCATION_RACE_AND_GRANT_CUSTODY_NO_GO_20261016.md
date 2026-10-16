# Physical PostgreSQL revocation ordering and owner-grant custody

**October 16, 2026 | Fleet Commander x1-370 | DRAFT, CRITICAL NO-GO**

## Scope

Draft #399 physically verified PostgreSQL 17 role separation, narrow
SECURITY DEFINER functions, uniqueness and owner-term checks. This
stacked experiment tests actual concurrent effects and revocation as
well as the difference between numeric monotonicity and independently
authorized new owner grants.

All connections, identities, effects, PostgreSQL credentials, and data
are ephemeral synthetic fixtures under one x1-370 administrator.
No production data or resources are involved.

## Physical transaction orderings and results

**Revocation first:** A restricted fx_revoker connection starts BEGIN,
executes fx.rotate and holds the authority row lock across pg_sleep.
A restricted fx_executor in a separate authenticated PostgreSQL
connection attempts to consume the old owner term. A superuser
observer checks pg_stat_activity wait_event_type='Lock' while the
executor is blocked. Once the revoker commits, the executor denies.
The restricted observer confirms zero effect rows for sequence 18.

**Execution first:** A restricted fx_executor calls fx.execute_effect
inside BEGIN and holds its lock. The revoker then waits on the
same PostgreSQL authority row, with a real Lock event witnessed.
The executor commits sequence 19, then the revoker commits.
This confirms normal revocation is prospective, not retroactive.

**Arbitrary owner grant NO-GO:** The restricted revoker can rotate to
arbitrary-owner with a higher term/epoch and revoked=false. An
authenticated executor can record an effect for that owner, without
any independently signed owner grant or external term provenance.

**Revived owner NO-GO:** After revocation, the same restricted
revoker rotates to a higher term/epoch and assigns previously
revoked owner-a with revoked=false. A new effect succeeds.
Monotonic sequence checking protects numbers, not who is
authorized to grant a resource execution role.

The first attempt at the reverse race used an already-consumed test
nonce; this fixture error was corrected. Two full reruns of both
race orderings passed, with PostgreSQL lock waits explicitly
observed. Failed test fixture attempts are not counted as proof.

The runner created one cached PostgreSQL 17 container with
network=none, read-only root, no published ports or host mounts,
tmpfs storage, dropped capabilities, no-new-privileges, bounded
RAM/PIDs/CPU, random synthetic SCRAM role passwords, and cleanup
on EXIT/INT/TERM. Both complete runs left zero labeled containers
and zero temporary race scratch directories.

## Proven boundary

A real shared PostgreSQL row lock correctly serializes **ordinary**
effect and revocation transactions that pass through restricted
database roles/functions.

It cannot establish independently authorized owner grants,
non-exportable signer key custody, real committed external effects,
anti-rollback history, or cross-database execution fencing.
In the toy procedure the revoker database password is sufficient
to select an arbitrary new owner. This does not mean regranting a
legitimate owner is always forbidden; the missing property is
independent authentication of the grant.

The #399 physical superuser counterexamples remain: direct SQL
can forge an effect, rewind the authority table, and make a previous
owner execute again. The executor can also provide an arbitrary
synthetic effect digest. Common host/Docker admin custody defeats
claims of separately governed signer/resource authority.

## Reproduce, opt-in only

Source-only, without Docker:

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/test_disposable_pg_effect_authority_contract.py tests/test_disposable_pg_revocation_race_contract.py

Actual disposable PostgreSQL 17 (requires deliberate opt-in):

    ALLOW_DISPOSABLE_PG_EFFECT_AUTHORITY=YES_SYNTHETIC_ONLY bash scripts/probe-disposable-pg-effect-authority.sh

## Remaining NO-GO

Independently issue and verify protected owner/epoch term grants
outside the source/anchor/witness DBA, Docker/host administrator and
backup authority. A real resource-owned executor must prove a
causally authorized physical effect and stable revocation at use
time. Negative cases: malicious restricted revoker, SQL superuser,
colluding signers, root-pin replacement, external witness/backup
restore, split-brain, effect-time mutation and uncertain ACK.
No automatic retry, merge, deployment or production change.

Auto-Assist #156/#299, Context #46/#31/#45, Router owner receipts,
model admission and physical iOS release all remain NO-GO.
