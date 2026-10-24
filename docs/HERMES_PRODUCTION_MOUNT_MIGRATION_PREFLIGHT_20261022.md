# Hermes production mount migration preflight — October 22, 2026

**Decision: HOLD.** Research-only work for draft auto-assist PR #425 and P0 issue #414. No production restart, mount edit, protected-file read, Docker object creation, secret copy, agent dispatch, or provider request was performed.

## Executed read-only audit

Run from the auto-assist worktree:

    python3 tests/hermes_production_mount_migration_preflight.py --audit-production-readonly --json

The preflight inspects only Docker Mounts, Image and State.Running, then verifies the existing synthetic restricted-Compose profile with docker compose config. It never inspects the container environment or sensitive file contents. Omit the explicit flag for a no-Docker default HOLD report.

## Physical production findings

- Currently running original Hermes image: sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307.
- 16 mounts total, 9 writable, 13 with at least one risk marker. The 13 include read-only SSH and source-alias concerns; they are NOT 13 writable mounts.
- Writable /home/scott/knowledge is a symlink to /media/scott/SSD_4TB/knowledge and maps to /root/knowledge.
- Writable /media/scott/SSD_4TB exposes the ancestor of that same protected evidence corpus.
- Writable /media/scott/NAS5 exposes the entire NAS root.
- Mutable adapter /app/src and several writable Git repositories must not be carried into a restricted worker.
- The container carries a read-only private SSH key/config; read-only does not make private key access safe.
- RW Hermes home/session/cache and evaluator outputs require explicit reviewed scratch/output custody.

## Narrow migration requirements

| Dependency | Existing production | Restricted synthetic candidate | Release acceptance needed |
| --- | --- | --- | --- |
| Input corpus | RW SSD ancestor and corpus alias | RO /research/knowledge | Operator-reviewed narrow RO path, no ancestor aliases |
| Self-task results and evaluation | Conflated legacy knowledge RW | RW /workspace/knowledge | Separately owned output and persistence proof |
| Hermes home/session/config/cache | RW SSD Hermes home | RW /workspace/hermes-home | Approved writable scratch capacity and custody |
| NAS5 | Whole root RW | No NAS mount | Deny root and require separate path review |
| Application source/repos | Mutable bind mounts | Exact code in pinned image | Signed immutable image provenance |
| SSH and provider secrets | Production private SSH/config and sensitive env | No key mounts/real provider calls | Independent explicit limited credential grants |

The restricted candidate has exactly two synthetic mounts, with UID 1000, network none, read-only rootfs, dropped capabilities, no-new-privileges and no ports. A previous exact patched-source synthetic normal-entrypoint check passed two restarts; it does not establish genuine owner-authorized ingestion or distributed fencing.

## Tests and blockers

The focused offline suite includes ancestor containment, symlink/alias detection, NAS risk, private SSH mount classification, unreviewed RW mounts, and strict negative Compose checks. The audited production configuration is expected to fail release qualification. The migration preflight reports **HOLD** even when the synthetic Compose rendering is compliant. Independent approval of real read-only sources, dedicated scratch, signed image/source, owner-bound task receipts, revocation/fencing, rollback and security review remain open. Do not merge/deploy or close P0 #414 based on this evidence.

## Host topology follow-up

Mount classification is now supplemented by [HERMES_STORAGE_TOPOLOGY_HOLD_20261022.md](HERMES_STORAGE_TOPOLOGY_HOLD_20261022.md). On x1-370 the NAS5-labelled host directory is not an actual mountpoint, and scratch is local-root research storage rather than approved production custody. The transition remains HOLD.
