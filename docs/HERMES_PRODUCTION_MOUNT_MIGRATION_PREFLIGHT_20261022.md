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

## October 24 afternoon — additive Compose overrides are NOT a safe cutover

The exact active `x1-370` Hermes Compose source pair was read back without inspecting or rendering any secret-bearing `environment` or `env_file` values. `docker-compose.yml` SHA256 `c598217dde96831ec55cd2a0bb0ddcdcb9a76743abb3063af0e0d0997e6fac1d` declares the existing **16** Hermes bind destinations (**9 writable**); `compose.override.yml` SHA256 `d726a3a2b98ad16bec53168cb586638e022e64ce71933fc9e84462928e8814a0` has no Hermes volume override. A wholly disposable, synthetic Docker Compose v5.6.0 *config-render-only* experiment demonstrated that putting `/research/knowledge:ro` and `/workspace:rw` into a second Compose file **adds those destinations without deleting** `/root/knowledge` or even a read-only private SSH key from the original base. A 2-bind overlay applied atop the existing 16-bind service would therefore still expose the old protected source and secrets. The live production configuration was not edited or rendered with substituted credentials.

`tests/hermes_compose_overlay_cutover_guard.py` is a new **deny-only metadata witness**, inert by default, and `--physical-readonly` is restricted to nonroot `x1-370`. It pins both source SHA256 hashes and reads only static YAML `services.hermes-adapter.volumes` declarations. It requires the exact 16 known legacy mount destinations and reports the original writable count, whether the deployed override changes mounts, and a mandatory **separately qualified standalone replacement**. It always returns `decision=HOLD`, `overlay_on_production_compose_authorized=false`, `production_deployment_authorized=false`. It does not inspect provider credentials, start Docker containers, read corpus contents, create files outside developer research, or change the production Compose files.

The companion static `inspect_effective_replacement()` shape checker rejects any of the 16 legacy targets, anything other than exactly the two narrow candidate binds, reversed RO/RW access, absent `bind.create_host_path:false`, protected SSD/NAS-label/Git/SSH host sources, broad `/nas` root, mutable root filesystem, root execution, unpinned image references, unexpected privileged capabilities or host networking. It can only establish a **research candidate's mount shape**: it does not independently sign images, approve corpus/custody/output ACLs or grant real credentials. The existing restricted synthetic normal-entrypoint profile remains the only tested two-bind fixture, not a production replacement. The read-only witness and its negative tests do NOT constitute a real production migration.

**Safe migration principle:** replace the existing service with an independently qualified isolated configuration, rather than layering an attractive two-mount overlay on top of the old service. Explicitly verify the *effective* container mount list after creation **before** any task admission. Preserve a controlled rollback and never restore the original broadly mounted service as an unreviewed automatic fallback. No real Hermes deployment/restart has occurred. P0 #414 remains open and PR #425 remains DRAFT/HOLD.
