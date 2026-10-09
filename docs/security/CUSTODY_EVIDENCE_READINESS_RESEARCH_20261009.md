# Custody evidence readiness gate — no secret access, no release authority

**Date:** 2026-10-09, 16:15–16:20 America/New_York  
**Parent:** [draft AssistX integration #235](https://github.com/scottjoyner/auto-assist/pull/235) head `3fdd750d87df1a8e4b51ea5cbd5677a27e00feda`  
**Blocking issue:** [#156](https://github.com/scottjoyner/auto-assist/issues/156)  
**Scope:** research-only metadata evaluator. Zero hosted model generations or production changes.

## Operator security context

- As checked via GitHub repository metadata on October 9, 2026, `scottjoyner/auto-assist` is **public**.
- Existing owner-private analyses recorded on issue #156 identify matching historically exposed, credential-named configuration entries across API/worker/Hermes consumers. This tool **does not reproduce those sensitive comparisons** or check credential validity.
- Both `.env.bak-sync-20261006T051130Z` and `.env.reconciliation.example` remain tracked on the tested integration branch. Their names are previously known from the pre-existing CI assertion; their **contents are never opened** by this tool.
- Restricting repository visibility alone cannot undo historical copies, forks, cloned Git objects, Actions logs, or credentials already used elsewhere.

## Hypotheses and observed controls

1. A filename-only Git index check should find tracked environment variants without reading file bytes or publishing their names. **Observed:** two suspect paths reported only as count `2` on physical x1-370; no file contents or container environment queried.
2. A submitted owner-checkpoint matrix is **untrusted** until the owner validates private rotation, dependent rollout, historical exposure impact, and an independent release security review. **Observed:** even seven synthetically submitted checkpoints produce unconditional `HOLD` and `production_authorized=false`.
3. Incomplete/unknown field sets, non-string state types, path-like references, forged free text, and out-of-order submissions fail closed and never echo their contents. **Observed:** 15/15 standalone Python unit tests passed, including malformed JSON-like states.
4. Automated CI can verify this narrow contract without pretending to prove credential rotation. **Pending:** exact-head GitHub Actions scoped check on this branch.

## Required owner-controlled checkpoints

The seven categories in `scripts/custody_evidence_readiness.py` represent **metadata submission states, not approval or proof**:

1. Public exposure containment, including the effect on active repository users.
2. Owner-private classification of potentially exposed credentials.
3. Coordinated issuer revocation and rotation.
4. Verified dependent API/worker/Hermes consumer rollout and rollback.
5. Public Git history, clones, Actions artifacts, caches and other distribution response.
6. Independent post-rotation verification performed privately.
7. Credential owner and security reviewer signoff.

Checkpoint references are **opaque IDs** matching the strict `CUSTODY-...` format. Never include usernames, exact key names, token values, endpoints with credential parameters, hashes of secrets, or public links to sensitive logs in this metadata JSON. Checkpoint submission is *not* cryptographically authenticated; separate private workflow and release governance still govern trust.

## Reproduction and non-goals

`python3 -m unittest discover -s tests -p 'test_custody_evidence_readiness.py' -v`

`python3 scripts/custody_evidence_readiness.py --repo .`

The second command exits **1 intentionally**, with a JSON HOLD observation, even if all synthetic claims are submitted and all tracked paths have been safely removed from the current tree. The SHA-256 digest covers **only that metadata observation** and is **not** an off-host immutable witness.

This project must **not** read Git blobs from historical environment backups, output secrets/hashes, delete or rewrite tracked secret-bearing files, modify repository visibility, rotate/restart services, grant execution, or waive the pre-existing `test_no_live_env_variant_is_already_tracked` failure. Deleting tracked files from the current tree alone is not proof of historical remediation.

**Disposition:** issue #156 and integrated release remain P0 **NO-GO**. Separate gates #149 (trusted ingress), #148 (capacity/cancellation), authenticated provider quota/cost and independent trace custody also remain unproven.
