# Hermes signed context: re-verify at consumption — October 22, 2026

**HOLD / research only.** Draft PR #425. No production deployment, mount change, key transfer, real agent execution or protected-data access.

## Gap closed

The earlier signed corpus startup gate authenticates a bounded manifest and checks its files when a new Hermes process starts. However, the live agent used `_gather_knowledge_context` to read the same files again later without checking digests. Separately, `swarm_memory.vault_semantic_context` may prefer an old persisted semantic index over freshly read corpus text. A privileged host could modify its backing filesystem after container startup even if Hermes sees a read-only bind.

## New opt-in read-time behavior

For `HERMES_REQUIRE_SIGNED_CORPUS=true`, the production adapter now calls `hermes_corpus_custody.verified_context_texts` immediately when collecting self-task model context. The helper re-verifies the signed manifest and all allowlisted bounded corpus inputs; it reads each candidate again through a single O_NOFOLLOW file descriptor, checks that digest against the signed entry, and returns the complete captured text list **only after all checks pass**. Bad signatures, extra/removed entries, changed content, path escapes, and an edit between the first and second read fail closed. The adapter refuses model dispatch when context re-validation fails.

The alternative semantic-memory index has no corpus-manifest ID, version binding, revocation or high-water marker. **In signed mode, index builds, persisted-index loads, and semantic searches are explicitly denied** until an independently designed signed index format is available. The adapter uses verified text instead. The legacy/unsigned workflow stays unchanged when the signed flag is unset.

This adds bounded I/O: verification checks at most 1,024 files, up to 4 MiB/file and 64 MiB total, per context collection. No claim is made about scaling to a complete NAS dataset.

## Verified evidence

On x1-370, the expanded focused suite passed **228/228** tests. New tests cover after-start file mutation/deletion, added unsigned files and symlinks, wrong signing key, a change between signed manifest validation and consumption, late-file failure, refusal to call the model after tampering, and blocking previously persisted semantic-memory content. Tests use only disposable synthetic files and test-owned ephemeral keys.

The exact patched-code research Compose canary also passed **11/11** physical checks: a new temporary networkless rootless image, signed synthetic read-only corpus and isolated writable scratch, two complete normal Hermes entrypoint starts, synthetic evaluation persistence, and a third restart denied after the synthetic host modified corpus bytes. All disposable containers and images were removed and original production Hermes remained running. The physical check proves protected startup/restart; the after-start revalidation is directly exercised with synthetic unit tests, not independently measured using a privileged concurrent host mutation on a live model call.

## Remaining explicit NO-GO gates

- **Replay:** the signed manifest has no independently anchored monotonically increasing version; a previously valid and fully matching signed corpus can be replayed.
- **Independent custody:** the signed test key and source both belong to the test runner; production signer identity, revocation, pinned trust-root change control, and independently signed source/image provenance are not verified.
- **TOCTOU:** the returned content is captured and hash-checked in memory, but a powerful host or separate reader may still change files outside this function's scope; mount-level trust and per-execution grants remain mandatory.
- **Semantic memory:** signed-mode semantic retrieval is disabled, not cryptographically upgraded.
- **Production mounts:** current Hermes still exposes broad writable SSD/NAS-labelled/repository aliases and private SSH access (16 mounts, 9 RW). Beelink's real mounted Btrfs/exFAT volumes have not received authorized read/custody grants.
- **Release:** real owner-bound task receipts, independent revocation/fencing, distributed high-water checkpoints, rollback, exact signed image, and independent reviewer signoff remain open.

Do not merge, enable signed data ingestion, remount NAS5, or restart production Hermes based on this synthetic gate. P0 issue #414 remains open and release **HOLD**.

## Optional image-pinned replay checkpoint follow-up

A second, distinct Ed25519 witness key can pin an exact signed manifest hash to a newer checkpoint in the restricted synthetic candidate. Older signed manifests are denied against the pinned newer checkpoint. **Co-rollback of both the corpus and the witness checkpoint is still accepted**, confirming that independent durable high-water custody is unresolved. See [HERMES_IMAGE_PINNED_CHECKPOINT_REPLAY_HOLD_20261022.md](HERMES_IMAGE_PINNED_CHECKPOINT_REPLAY_HOLD_20261022.md). Production HOLD is unchanged.
