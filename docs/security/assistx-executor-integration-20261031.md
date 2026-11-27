# AssistX executor security integration — October 31, 2026

This branch composes four independently tested **drafts** into an isolated source-only integration candidate. It is not a production approval, deployment, permission grant, or claim of physical validation.

## Exact source pins

| Source | Head SHA | Scope |
| --- | --- | --- |
| PR #451 | `caf6878e17f14d82ad333c2349ca266476534a8f` | Claimed payload, explicit empty tool and model alias issuance controls |
| PR #452 | `8c1bf1d66bf3c6d4b805828dbb167bbe78bad40a` | Claim-snapshot identity, source payload, model and repository task binding |
| PR #453 | `0b17a03286afecee6528532afc043b3a87727699` | Strict direct inference uses the current client's JWT, not service-token fallback |
| PR #454 | `6f55b9a034d581bb809f2864816fcfafb1e89f06` | Model directory nofollow confinement, legacy naming HOLD, workspace admission |

## Combined negative acceptance

- Malformed/duplicate canonical JSON, empty explicit tool scope and disallowed model IDs must not mint broader executor authority.
- Poll snapshot A differs from claimed snapshot B: the worker must derive model, prompt and work packet only from B, with matching claim/session/lease.
- Missing/revoked/expired local claim, disallowed model or absent scoped bearer must not dispatch direct inference. The resource router must still verify the real JWT signature, owner and current revocation.
- Rejected workspace paths (dot segments, legacy conflicts, symlinked root/child/note, permissive directory mode) must stop repository preparation and inference.
- Synthetic mocks and filesystem temporary directories are not physical fleet or independently trusted resource-side receipts.

## Unresolved HOLD / NO-GO gates

1. Independent sign/revoke custody (#426) and an externally anchored non-restorable monotonic current-claim/effect ledger.
2. Resource-owned effect admission before tool execution, repository modification and inference; a source snapshot or worker self-assertion is insufficient.
3. Removal of broad writable Hermes mounts (#414) and separately reviewed authoritative graph privilege isolation.
4. Signed OCI provenance/custody, image and Compose digests, owner-approved negative recovery and no-dispatch rollback.
5. Approved inventory/migration policy for old namespaced workspaces and coordinated HERMES_KNOWLEDGE_ROOT, HERMES_EVAL_PATH and swarm_memory.MEMORY_DIR.
6. Exact-head hosted CI with executed steps, integration negative tests and independent restricted disposable-stage acceptance before any merge/deployment.

No running fleet service, live task, database, key, NAS or production filesystem changes are authorized by this branch.
