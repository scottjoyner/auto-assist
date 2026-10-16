# Disposable PostgreSQL restricted effect issuer: better role separation, not trusted resource ownership

**October 16, 2026 · Fleet Commander x1-370 · DRAFT / SECURITY NO-GO**

## Why this is a new experiment after Auto-Assist #399

#399 physically proved in PostgreSQL 17 that a restricted effect executor
cannot directly write effect tables, rotate authority, or bypass ordinary
term/epoch checks, but that very executor could call its permitted effect
procedure with an arbitrary plausible digest. This experiment adds a
**separately authenticated issuer** who must first record the *exact*
resource/term/owner/sequence/nonce/digest claim before the restricted
executor can apply it. It revokes the original unrestricted executor
entrypoint to prevent bypass.

This is **not** a cryptographic signature, trusted resource-COMMIT receipt,
independent administrative domain, or production execution path.

## Implementation: conditional control limited to one toy database

- New disposable-only SQL overlay `disposable-pg-effect-admission.sql`
  runs on #399's toy `fx` schema and adds one `fx_issuer` login.
  `fx_issuer` is NOSUPERUSER, NOCREATEDB, NOCREATEROLE and NOINHERIT.
  It can only execute a `SECURITY DEFINER` function
  `fx.issue_claim`; it has no direct table mutation rights.
- A private `fx.authorizations` table contains unique sequence/nonce,
  exact term, owner, epoch and a single-use `spent` flag. Restricted
  readers can SELECT but no restricted caller can INSERT/UPDATE/DELETE.
- `fx.execute_issued` may be called only by `fx_executor`; it locks
  the current owner/term/epoch/revocation row with `FOR UPDATE`,
  atomically consumes **one exact** matching unspent claim and records
  an effect. If the effect INSERT fails, the spent update rolls back.
  Its earlier bypass `fx.execute_effect` is REVOKEd from executor.
  Function bodies use `SECURITY DEFINER SET search_path=pg_catalog`
  and explicitly qualified `fx` relations; PUBLIC EXECUTE is revoked.
- A dedicated physical opt-in runner requires
  `ALLOW_DISPOSABLE_PG_EFFECT_ADMISSION=YES_SYNTHETIC_ONLY`; uses only
  the already cached `postgres:17-alpine`, random short-lived SCRAM
  passwords, no network, no mounted volumes or ports, read-only root,
  dropped Linux capabilities, no-new-privileges, tmpfs state, resource
  limits and trap cleanup. No image pull, production DB/network,
  fleet SSH, credentials or host system settings.

## Physically reproduced positive controls

On x1-370, a live isolated PostgreSQL 17 cluster denies the following:
executor calling the old arbitrary-digest entrypoint, executor issuing,
issuer executing, issuer directly writing authorizations, executor
unspending a receipt, revoker/observer issuing, effects without prior
claim, substituted SHA digest or nonce, duplicate issuance, and reusing
a consumed authorization. These denials use *actual authenticated*
PostgreSQL sessions with separate SCRAM credentials.

An issuer-approved claim is consumed exactly once by the executor.
Both term/epoch rotation and revocation AFTER issuance but BEFORE
execution deny the old authorization. Two independent concurrent
executor psql sessions racing over the same authorization show **one
committed effect and one denial**; the claim is consumed only once.

A fresh restricted observer reads back the exact effect and spent
authorization. Cleanup readback: **zero research-labeled containers
remaining** after the physical run.

## Critical passing negative controls (production NO-GO)

1. **Issuer without actual resource COMMIT can approve fiction.**
   A restricted issuer signs *nothing* and merely stores a valid-looking
   arbitrary claim. The executor consumes the claim and records an
   effect. Separation of role identities does not validate source data.
2. **Database superuser can bypass both parties.** The superuser directly
   inserts a fictitious `fx.applied` row; the restricted observer
   cannot distinguish it from a permitted procedure result.
3. **Superuser can rewind owner-term revocation.** A privileged direct
   table UPDATE restores a previously authorized owner/term/epoch;
   issuer and executor can then accept an old-era effect.
4. **All roles and tables share one Docker host administration**.
   A Docker/system/database administrator can act as PostgreSQL
   superuser. Different logins cannot prove independent key, revocation,
   backup, root or resource-owned custody.
5. **No cross-system effect attestation:** the toy procedure does not
   verify independently authenticated source COMMIT, owner-bound
   physical resource effects, signer/root revocation, external
   monotonic latest high-water, or real network/model execution.

The reported PASS means the **restricted-call contract** was reproduced,
not that the provenance or production-security gate passed.

## Reproduction

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
      tests/test_disposable_pg_effect_admission_contract.py

    ALLOW_DISPOSABLE_PG_EFFECT_ADMISSION=YES_SYNTHETIC_ONLY \
      bash scripts/probe-disposable-pg-effect-admission.sh

The first command is source-only. The second explicitly launches an
ephemeral local PostgreSQL container and cleans it on exit. No private
secrets are printed or committed. No production authority is touched.

## Next decisive security proof

Any real future authorization must be issued by a **resource-owning
execution authority outside the source/anchor DBA, Docker administrator
and witness administrator control**, with independently protected
term/fence grants and non-exportable signing key. The actual effect
must be causally bound to an externally pinned, restore-resistant
monotonic history, verified at effect time and under revocation, with
no automatic replay after uncertain COMMIT. Demand falsification against
two colluding signers/DBAs, privileged storage restoration, forged
effect receipts, split-brain term changes and signer/root substitution.

**Keep Auto-Assist #156/#299, Context #46/#31/#45, Auto-Router
owner-bound receipts and physical iOS release on HOLD. NO MERGE,
DEPLOY, REAL GRAPH/DB, CREDENTIAL, NETWORK, TPM, MODEL, FAILOVER,
IOS OR AUTOMATIC-RETRY ACTION.**
