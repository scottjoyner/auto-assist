# Original AssistX image — physical emergency-auth compatibility rehearsal
**October 22, 2026 | Research candidate | Production HOLD**

## Physical candidate and source integrity

Fleet Commander used x1-370's exact running production `assistx-api` image `sha256:74df40c5c720dbef283f83eaf69eae585212748ca3889be508eaa9c88cd5b4c4` as a local, pinned build base. The only overlay files were the exact reviewed `src/assistx/api.py` (`git hash-object dddae05f2a27de56ac64135aa6a43ab030653fb7`) and a synthetic no-credentials runtime probe.

Docker's BuildKit does not treat a bare `sha256:` in `FROM` as a local image and initially attempted a registry resolution, which failed safely. The canary now creates a **temporary local alias** to that exact already-present image, builds the overlay with `--network=none --pull=false`, then deletes both its own derived candidate image and its temporary alias. The original image is not deleted or retagged and its running container is never stopped or restarted.

The candidate ran with `--network none`, no mounts, immutable root filesystem, UID/GID 65534, dropped Linux capabilities, no-new-privileges, limited processes/memory/CPU, synthetic credentials and exact source-compiled `_auth_user_from_credentials` from inside the patched actual runtime image. It did not start the production API entrypoint, connect to a live database, issue an LLM/model request or make any external network request.

## Physical results

**11/11 checks passed** in `tests/assistx_legacy_header_image_canary.py --physical-disposable-image`: pinned-image offline overlay built; patched API file present; emergency reject flag denies header-only authentication; the same flag rejects an unsolicited trusted-user header even alongside *valid* Basic credentials; valid Basic credentials without that header remain valid; invalid Basic credentials are denied; removing the flag **inside the disposable test process only** restores the legacy raw-header acceptance (negative control); the restricted candidate was networkless and mountless; both production API and Hermes remained on their exact original images, with Hermes's 16 legacy mounts; all disposable images and aliases were removed.

The canary's default invocation is inert. It pins both running production image identities and the reviewed source Git blob. Its static tests reject accidental addition of live Docker stop/restart/exec, image removal by production SHA, host mounts, or network-capable test commands. These tests are not proof of compatibility for all fleet clients: the auth helper's behavior was exercised inside the real image but not via a full running API or through every client workflow.

## Client/proxy dependency evidence

Read-only inventory found `control-room-ts-proxy.service` actively forwarding raw TCP from the Tailscale IPv4 address port 8000 to the local AssistX API 127.0.0.1:8000, with the exact public OpenAPI schema at both endpoints. `assistx-tailnet-home/server.py` probes a localhost AssistX health endpoint, and `super-awesome-ai-gate.service.d/90-assistx-local.conf` references the localhost API. These references **do not establish** whether any client relies on the legacy trusted-user identity header. The proxy's TCP forwarding does not validate or strip that header.

The production API still has the legacy header feature configured, with emergency rejection **not enabled** and owner-bound signed gateway disabled. It remains susceptible to *conditional trusted-header spoofing* by Tailnet clients that can reach the forward and supply the configured header. No live impersonation attempt has been made, and no public Internet access has been claimed.

## Candidate rollout/rollback gate — not executed

Before any live cutover, enumerate currently authenticated fleet clients and header-only dependencies without collecting credentials or revealing header names. Prepare a separately authenticated edge that strips all client identity headers, or remove the legacy header trust when clients can authenticate with Basic or another validated mechanism. Pin and separately run an exact new API image, check 401 for untrusted header-only synthetic requests, check authorized Basic requests without headers, verify health and all primary clients, and test rollback without letting the unsafe header route persist. **Do not set the emergency flag on the existing old image** (it does not contain the new implementation), and do not interrupt the proxy before a complete substitute and rollback exist.

Independent user authentication, signed gateway custody, revocation high-water, owner-bound real task receipts, the 16-mount source/scratch remediation, signed artifact provenance and disaster recovery remain NO-GO. Draft PR #425 and P0 issue #414 remain open, with production unchanged.
