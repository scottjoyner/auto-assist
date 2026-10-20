# Pre-publication environment-file custody gate — October 11, 2026

**Disposition: metadata-only enforcement, not credential-rotation approval.** Related [P0 issue #156](https://github.com/scottjoyner/auto-assist/issues/156), [draft security PR #293](https://github.com/scottjoyner/auto-assist/pull/293), and sensitive research repair [PR #291](https://github.com/scottjoyner/auto-assist/pull/291).

## Why a final clean PR diff is not enough

A commit that **adds a sensitive environment file** remains reachable from the public repository even if a later commit deletes the file. A name/status check of only `git diff BASE HEAD` would miss an add-then-delete sequence when the final PR diff contains no such file. The same is true for an add-then-delete pair inside a merged side branch. Historical commit patches, PR revisions and caches may already be visible.

The guard now compares **both** the final base-to-head filename/status diff and **every commit's first-parent filename/status delta** across the full branch ancestry since `git merge-base BASE HEAD`. The ancestry set includes merged side branches; `--no-renames` ensures old/new filename identities are not hidden by rename detection. `--no-ext-diff` and `--no-textconv` explicitly prevent external diff drivers and blob conversions. No `git show`, `git cat-file`, `git log -p`, file-body read, key-name list, credential value, or historical patch is needed.

**Fail-closed outcomes:**
- `HOLD_PUBLIC_ENV_HISTORY`: any sensitive basename was added, changed, or removed in a reachable commit, even if the final PR diff is clean.
- `HOLD_PUBLIC_ENV_DIFF`: final sensitive-name changes detected (and no historical change observed, such as an unusual tree comparison).
- `HOLD_METADATA_UNAVAILABLE`: ancestry, commit metadata or bounded checks cannot be established (including >256 branch commits). A holding result is not grounds for a CI override.
- `PASS_NO_ENV_DIFF`: no sensitive path changes were detected under this **filename-only** policy. It **does not** mean older tracked credentials were revoked, deleted from history, safe to publish, or the system is deployable.

The auditor outputs only counts/statuses, with `reads_file_contents=false`, `credential_custody_closed=false`, `production_release_authorized=false`. It does not output sensitive filenames. It cannot detect secrets in ordinary filenames or prove that contents are safe.

## Run before publishing any branch

After reviewing the intended upstream branch and only using local commit metadata:

```sh
BASE="$(git merge-base origin/main HEAD)"
HEAD="$(git rev-parse HEAD)"
python3 scripts/audit_sensitive_pr_patch_names.py --base "$BASE" --head "$HEAD"
```

Use the actual target branch rather than `origin/main` if different. **Do this before a push**, because GitHub Actions checks after publication and cannot retract a disclosed deletion patch. A clean result is *not* authorization to push a candidate containing known, previously tracked secret-bearing files. Coordinate any necessary history/visibility change directly with the repository owner and credential custodian.

The PR workflow audits the GitHub-reported PR base/head; a separate push-to-main step detects post-push sensitive path changes. Neither step is a replacement for branch protection, secret scanning, private custody assessment, or human review.

## Evidence on actual candidate ancestry (metadata only)

| Candidate | Commit ancestry checked | Net sensitive names | Historical sensitive changes | Result |
|---|---:|---:|---:|---|
| AssistX repair PR #291 at `41c37863` | 8 commits | 3 (1 addition, 2 deletions) | 3 changes across 3 commits | **HOLD_PUBLIC_ENV_HISTORY** |
| The safety guard PR #293 at `713efd45` | 7 commits | 0 | 0 | **PASS_NO_ENV_DIFF**, no security-custody claim |

The older guard already held PR #291 because the **net** diff was sensitive. The stronger history test also detects a clean-net but exposed-intermediate-commit sequence.

Local synthetic acceptance on x1-370: **31 tests passed**, including sequential add/delete, merged-side-branch add/delete, harmless changes, and bounded ancestry fail-closed behavior. All fake `.env` fixtures contain only synthetic placeholder text. No live credential values or hashes were opened, logged or transferred.

## Existing owner-private incident custody — separate mandatory P0

The owner-private value-free checker last reported **14 credential-named configurations matching a historical tracked backup** across running AssistX consumers. The private rotation manifest records **14 owner assignments required and 14 rotations not started**. This is continued matching configuration, not proof of token validity or exploitation. These details were already documented in [issue #156](https://github.com/scottjoyner/auto-assist/issues/156).

Do not copy the owner-private manifest into GitHub, disclose individual names/values, auto-rotate production credentials, print deleted Git patches, or rewrite history without owner coordination. Required before release: custodian assignments, classification, issuer-side revocation/rotation, dependent API/worker/Hermes transitions, postrotation verification, and review of public Git/PR/cache/CI/fork exposure.

**Promotion authority remains `NO_GO` even when this guard's own CI is green.**
