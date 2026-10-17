# AssistX / Auto-Router runtime image custody — October 17, 2026

**Evidence: Fleet Commander x1-370 production READ-ONLY plus isolated source tests.**
**Decision: DRAFT / HOLD / NO-GO. No production rollout or restart.**

## New read-only verifier

- scripts/read_only_image_custody.py supplements AssistX PR407's
  Docker container/Neo4j environment reference audit. It accepts a fixed
  five-name container allowlist: AssistX API, worker, Hermes adapter,
  production Neo4j, and Auto-Router.
- The verifier refuses DOCKER_HOST, DOCKER_CONTEXT, DOCKER_CONFIG, TLS
  overrides or any Docker context other than default. It also checks the
  effective context endpoint equals unix:///var/run/docker.sock;
  a default-*named* but remote-configured daemon is rejected.
- It invokes ONLY local Docker context show/inspect, Docker info,
  Docker container inspect, and Docker image inspect. No network writes,
  image pulls, docker exec, script/SQL/Bolt access, secrets, or restarts.
- Every container's immutable 64-hex container ID, local sha256 image
  identifier, running status, start timestamp, PID, network-name inventory,
  and mount-access category counts are matched against its actual
  locally inspected image object. Manifest RepoDigest values are parsed
  and reported without registry names. Local image/config digest and
  registry manifest digest are intentionally NOT conflated.
- The full sanitized inventory is independently sampled twice around
  three equal observed Docker daemon identities. A changed daemon,
  container ID, running process PID/time, network, mount-access category,
  image ID, revision claim, or manifest digest FAILS CLOSED.
- No raw Docker Config.Env, mount Source/Destination, image labels,
  registry host/path, credentials, Docker stderr or stdout are printed.
  A valid-format org.opencontainers.image.revision is strictly an
  UNSIGNED CLAIM, never proof of source-to-image provenance.
- EVERY successful result reports production_decision=HOLD,
  promotion_authorized=false, signed_source_to_image_verified=false.
  This module has no codepath to sign or accept an attestation, deploy,
  rotate credentials, load models or enable fleet failover.

## Executed physical observation

On the existing production Docker daemon at x1-370:

| Workload | Local image/object consistency | Repo digest(s) | Source revision label |
| --- | --- | ---: | --- |
| AssistX API | Stable across two inspections | 1 | Missing |
| AssistX worker | Stable across two inspections | 1 | Missing |
| AssistX Hermes adapter | Stable across two inspections | 1 | Missing |
| Neo4j | Stable across two inspections | 1 | Missing |
| Auto-Router | Stable across two inspections | 1 | Missing |

All were running, and no container/image identity mismatches were
observed in this sample. The Neo4j container attached to three Docker
networks; the router to two; API/worker/Hermes each to one. This is not
proof of graph network isolation, build integrity, builder independence,
or future process continuity.

Security checks:

- **31 dedicated adversarial/source tests PASSED.**
- Ruff and Python compilation PASSED.
- Existing local image-bundle test **2 passed**.
- The older local operational checkout's unrelated RepositorySourceBinding
  contract suite failed 1 test after 5 passes because the checkout lacks
  from_contract_payload. The CURRENT GitHub main and exact PR407 source
  both contain that method; do not generalize this stale-checkout failure
  to the newly added script or silently mutate production source.
- The physical observation returned HOLD and no signed attestation.
  No product service/network/Neo4j/credential, host packages, model
  residency, or production image was modified. The code ran only from
  a private research source directory under /home/scott/git.

## Remaining P0 release gates

1. A separately governed builder/signing authority must issue a
   verifiable signed statement containing exact Git commit and build
   source tree, image artifact digest and build inputs, trusted
   verification public root and revocation/expiry policy.
2. Before release, independently verify registry manifest digest,
   local loaded image ID, running container ID/PID/start time,
   expected network/mount/UID/policies, and signed provenance with
   a separate operator-held root. Runtime Docker labels alone
   cannot supply that chain. This script deliberately returns HOLD.
3. Production Neo4j API/worker/Hermes still share broad administrative
   graph identity; dedicated Context-only DBMS/least-privilege
   migration and direct noncaller Bolt denial remain uncompleted.
4. Context #46 independent non-restorable resource COMMIT still fails
   co-restore (Xwing software grant journal + Lenovo SQLite) and
   privileged DB-owner bypass negatives.
5. Context hosted CI #45 remains zero executed runner steps due
   reported account spending/billing admission hold. Auto-Assist
   hosted CI for this exact new draft must also be checked.
6. Owner-bound receipt, supervisor process custody, positive and
   negative restricted canary, rollback, and explicit approval
   still required for any actual AssistX/Auto-Router promotion.

**No production deployment or automatic failover is authorized.**
