# Attested Tailnet gateway — receiver-owned replay fence research

**Date:** 2026-10-09. **Status:** tested in isolated research; **NOT a production gateway or deployable release**.

Related: [#149 trusted ingress](https://github.com/scottjoyner/auto-assist/issues/149), [#156 public key exposure](https://github.com/scottjoyner/auto-assist/issues/156), [draft #210 verifier](https://github.com/scottjoyner/auto-assist/pull/210), [draft #202 real same-bridge spoof proof](https://github.com/scottjoyner/auto-assist/pull/202).

## Purpose

A future verified Tailscale ingress can preserve the existing Kipnerter iOS Agent Auto UX (the current `.fleetAuto` client sends no HTTP Basic header) by forwarding a **signed, request-bound, short-lived per-user assertion** to a backend that otherwise rejects unverified identity headers. However, a signature by itself can be replayed across workers or after Redis restart.

`src/assistx/gateway_replay_fence_research.py` supplies an **unwired** receiver-side callback suitable for the existing `trusted_gateway_identity_contract.verify_gateway_assertion` verifier. It verifies a caller-provided externally approved Redis boot ID **before and after** an atomic Redis Lua `SET NX PX` nonce consumption, using Redis TIME and the signed claim's absolute expiry. Nonce keys HMAC the per-user subject and claim nonce under a receiver-owned key; Redis never stores raw usernames, claim bodies or signing keys.

The callback denies malformed subject/nonce/expiry, a missing/wrong boot pin, Redis INFO/EVAL outage, duplicate nonce, overlong/expired TTL, boolean/string/unexpected EVAL acknowledgment or a generation mismatch during consumption. It **never mints or approves** a new Redis generation.

## Independent exact-head acceptance

Commit `b2a9073331c3948f320d52ba6f3b3cb21f8301a2` on isolated x1-370 detached worktree:

- 44 focused offline tests **PASS** (20 existing Ed25519 verification tests, 18 new nonce/negative tests, 6 new Docker runner guards).
- Guarded real Redis 7 Alpine: single disposable `--network none`, no published ports or host mounts, read-only, no persistence, 0.25 CPU/128MiB, non-root, capabilities dropped. The script uses ephemeral test-only Ed25519 signing key and invented operator identity; no live Tailnet/client/provider/Neo4j or production credentials.
- **`REAL_REDIS_10_WORKER_SINGLE_USE_GATEWAY_IDENTITY_PASS`**: 10 parallel authentications of the *same* freshly signed, bound claim against real Redis; exactly **one** recipient accepted and the remaining **nine** denied on duplicate nonce.
- **`REAL_REDIS_RESTART_STALE_GATEWAY_ASSERTION_DENIED_PASS`**: restarting only the test Redis loses its nonce keys and changes process ID; the previously pinned receiver denies the signed assertion rather than re-admitting it. Container cleanup by recorded immutable ID.
- The existing offline GitHub workflow is extended to check test-only contracts; Docker is never started by the workflow.

## Do not overstate the result

1. A Redis `run_id` is **not durable receiver-owned generation authority**. It can change on restart, failover or promotion, and a worker that accepts a new ID without an independently signed/quiescent control-plane receipt can replay previously signed claims. A production-ready replay store needs durable anti-rollback custody or a separate independently witnessed durable nonce ledger, and an operator-controlled freeze/re-admission protocol.
2. `INFO server` is not atomic with the Lua operation. A generation transition immediately after the final check is an unproven race. The staged result does not protect an arbitrary network partition or disconnected proxy.
3. **No gateway signer exists in this change.** Tailscale Serve does not itself sign the user identity header for the backend; source authentication, signer isolation and its Ed25519 key custody remain to be built and independently tested. It would be unsafe for a signer on a shared Docker bridge to sign any received `Tailscale-User-Login` header.
4. The verifier does not grant an operator role. The backend must map signed identities to receiver-owned RBAC/quotas before Neo4j, and bind all routes and raw URL encodings.
5. Public tracked environment files under #156 remain a confirmed possible live-credential exposure. No live signed gateway should be provisioned using any historical credential until custodian-led rotation and repo history containment.
6. Real iOS Agent Auto/trace browser clients, Tailscale ingress path provenance, authenticated API 1/3/5/10 stress, failure-injection, rollback and RC2 source/CI gates remain separate.

**Keep this research unwired to FastAPI and keep #149 / #156 / production release NO-GO.**
