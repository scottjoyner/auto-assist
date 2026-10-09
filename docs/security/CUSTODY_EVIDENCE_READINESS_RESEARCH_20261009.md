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



## October 9, 2026, ~18:00 EDT — bounded pinned-history metadata extension

A new optional `--historical-commit` accepts **only one exact, 40-hex Git commit ID**. The checker performs `git cat-file -t` to ensure the ID refers to a **commit object** and `git ls-tree -r -z --name-only` to count suspicious historical pathnames. No file contents, values, commit bodies, historical secret hashes, or live container environments are read or surfaced. The existing current-index count remains independent.

Read-only x1-370 observation (commit name already identified publicly in issue #156): selected Git commit `db734eeeb7f50ba501c53f20ecb2fd30c2a6d5c9` has **two** tracked `.env`-family variant pathnames; the currently tested draft worktree also has **two**. The output includes only the counts and observation digest, and returns **HOLD / exit 1**. This is proof of filename presence in one pinned Git historical snapshot, *not* proof of secret validity, the scope of all reachable refs, repository visibility changes, rotation completion, or historical cleanup.

Additional negative tests simulate (a) deleting a sensitive filename from the current index while it remains in a pinned earlier commit, (b) a clean selected commit that still cannot authorize release, (c) arbitrary Git ref/revision expressions, (d) nonexistent IDs, and (e) Git tree-object hashes masquerading as commits. The standalone source branch has **20/20** tests expected once exact-head tests finish; use the CI status as authority for the actual count.

Example read-only invocation:
`python3 scripts/custody_evidence_readiness.py --repo . --historical-commit db734eeeb7f50ba501c53f20ecb2fd30c2a6d5c9`

GitHub Actions' default checkout may be shallow and **does not have to possess this pinned historical commit**. Its scoped CI tests the synthetically constructed historical Git snapshots offline instead. Any real-history check must use a verified local clone with the selected commit already available; do not add a broad public-history fetch merely to satisfy the test or mistake a missing commit for a clean history.

**No authorization:** A single historical tree is not an exhaustive commit/ref history, a private clone inventory or evidence of independently authenticated rotation; the tool always rejects production or merge authority. Do not open public PRs that reproduce deleted sensitive file contents.


## October 9, 2026, ~18:47–18:50 EDT — bounded locally reachable ref history

Research-only tool option: `--local-ref-cap 1..128`. The validator enumerates at most `cap + 1` commit IDs from **locally reachable Git refs** using `git rev-list --all --max-count` and inspects the first `cap` tree *filenames* only, with explicit subprocess timeouts, fixed argv, no shell, `GIT_NO_LAZY_FETCH=1`, and `GIT_NO_REPLACE_OBJECTS=1`. If another sampled commit exists, it sets `local_ref_sample_truncated=true`; a non-truncated result only covers local refs present in the checked clone, **not the public repository's complete distribution**. It never prints paths or commit IDs, never reads file content, and always returns HOLD.

**Physical x1-370, isolated read-only invocation on the #235 integration worktree:**

- Previously pinned specific historical commit: **2 suspicious tracked file pathnames**.
- Current Git index: **2 suspicious tracked pathnames**.
- Bounded 64-commit sample: **64/64 commits with suspicious `.env`-variant pathnames**, **2 distinct suspicious paths**, **truncated=true**.
- Bounded 128-commit sample: **128/128 commits with suspicious `.env`-variant pathnames**, **2 distinct suspicious paths**, **truncated=true**.
- Both samples exit with `status=HOLD` and exit status **1**. Neither is a test of secret validity, nor a guarantee of full history coverage. This is risk metadata, **not** independently certified public-credential exposure scope.

**Unit acceptance:** 25/25 Python `unittest` checks PASS on x1-370. Five additional negative tests cover capped-history truncation, previously removed tracked paths in earlier commits, invalid caps, unavailable Git, and distinct path counting without revealing names. Check the current PR head's exact GitHub Actions run for independent CI acceptance; previous head's green run is insufficient to accept this revision.

**Operator urgency:** P0 #156 owner-controlled public exposure containment, independent key revocation/rotation, dependent consumer recovery and rollback, private postrotation verification and historical/clone/artifact handling are still required; none performed by this metadata scan. Do not interpret one clean index or a capped-history sample as permission to merge, deploy, cut over Basic auth or dispatch free-provider workers.
