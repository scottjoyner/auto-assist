# Direct Docker-peer trusted-header negative acceptance (isolated)

Date: 2026-10-09. **Research only / production NO-GO** under [#149](https://github.com/scottjoyner/auto-assist/issues/149), [#156](https://github.com/scottjoyner/auto-assist/issues/156). Works atop opt-in strict-Basic code [#184](https://github.com/scottjoyner/auto-assist/pull/184).

## Observed initial x1-370 experiment

Two disposable instances of actual AssistX FastAPI from the same reviewed source tree were launched **sequentially** on a temporary Docker `--internal` bridge. Each was read-only, had no host published ports, and was accessed only by a newly created disposable Python HTTP peer on that bridge. Synthetic Basic user/password and synthetic `Tailscale-User-Login` identity were invented for the experiment; **no real values** or live `.env` files were mounted. Only `src/`, `templates/` and `static/` were mounted read-only; local SQLite/outbox state was isolated in tmpfs. Uvicorn ran with lifespan disabled to avoid unrelated background startup work. No Neo4j, Redis, providers, real users, or production API endpoints were contacted.

| Test from same-bridge peer | Legacy trusted-header | Strict Basic flag enabled |
|---|---:|---:|
| Anonymous `/fleet-dashboard` | 401 | 401 |
| Forged header, no Basic | **200** | **401** |
| Incorrect Basic + forged header | **200** | **401** |
| Valid synthetic Basic | 200 | 200 |
| Forged header on `/traces` | **200** | **401** |
| Forged header on `/api/fleet/dashboard` | 500* | **401** |

*Legacy 500 occurred **after** authentication in a disposable API with no graph backend; it must not be reported as successful API-data access or production exploit. It supports the source-level conclusion that the header bypasses authentication into route handling, but no production graph was touched.*

Both modes' peer tests passed; `BRIDGE_AUTH_DIFFERENTIAL_DISPOSABLE_PASS`. The temporary containers and network were cleaned up.

## Source-owned replay guard

`scripts/run_rc2_bridge_auth_isolated.py` requires explicit `--approve-disposable-bridge-auth`, exact clean Git source HEAD and pinned local SHA256 IDs for the server/client Docker images. It denies pre-existing names, non-internal networks, unexpected peer containers, host port publishing and non-read-only synthetic API state. It runs the same six HTTP test cases in strict/legacy modes, requires the two respective PASS markers, and cleans up only its own recorded container IDs/network. CI **never** invokes Docker or this canary; it runs `tests/test_rc2_bridge_auth_disposable_guard.py` only.

The original observations preceded committing this replay harness. **The exact-head guarded replay and GitHub CI must be independently checked** before accepting it as a reproducible canary. This document alone is not production acceptance.

## Why #149 remains open

- The live x1-370 API currently uses `TRUSTED_AUTH_HEADER=Tailscale-User-Login`, strict Basic was disabled at inspection time, and its two Docker bridges had seven distinct peers. A TCP-only disposable test also established direct container-to-container backend reachability without Tailscale Serve.
- Synthetic Basic negative tests do not verify the safety/rotation of production Basic credentials. Separate [#156](https://github.com/scottjoyner/auto-assist/issues/156) evidence identified live configuration entries matching a public tracked environment backup. Credential custody, owner-controlled rotation and history cleanup are essential before deploying Basic-dependent auth.
- Real iOS/Tailscale/mobile clients may depend on trusted-header-only authentication. Staged client compatibility, authenticated operator roles, consumer migration/rollback and independent ingress-path review remain required.
- This experiment proves the chosen opt-in mitigation rejects a spoofable header across the tested bridge path. It does not prove all runtime routes, proxies, worker host namespaces, app deployments or historical credentials are safe.

**Do not change production API flags, proxy networking or live credentials based on this experiment. Production remains NO-GO.**
