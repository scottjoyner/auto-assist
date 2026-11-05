# Fleet node machine identity is NOT Task effect authority

**Stacked security research; production HOLD.**

Source base: Auto-Assist draft PR #548 (per-node identity), exact parent SHA de1d93fc805a4c05afd7121cbffce5369db41496. Privileged HTTP transport is sourced from draft PR #547 at exact SHA b858d6ceaaace3e684b7ef660b4bf5924dbad9f6.

## Why this integration is required

The initial #548 path allowed an operator-registered per-node token to obtain a legacy READY Task list (and make legacy Task claim/heartbeat/finalization requests) when the claimed node ID matched. Those handlers rely on general-purpose operator-era graph Task operations and lack the independent machine-principal, signed projection, transactional claim generation, revocation and resource-accepted effect authority required in #501/#427/#428/#426. **A token check and matching query parameter are identification, not Task authorization.** Five production agents remain 401 and signed Task supply remains expired with zero approved slots.

The original #548 fleet-node agent HTTP implementation used default urllib.request.urlopen, whose redirect handling can forward a credential header to a different origin. The standalone #547 synthetic tests demonstrated this. Deploying the per-node token feature on that transport would create a credential disclosure path.

## This branch — deny-only improvement, not fleet rollout

- Every legacy agent Task endpoint (GET /api/agent/tasks, POST .../claim, heartbeat, complete, checkpoint) retains valid operator Basic authentication semantics, but **every node-credentialed request is rejected with HTTP 403 before graph access**, even if node ID/token are valid and match, even if Basic is supplied too.
- Missing/partial/wrong node identity still rejects; never silently falls back to operator Basic. A separate scoped executor and observer with independent machine admission must replace these legacy endpoints for machine callers before productive dispatch.
- The agent's privileged HTTP transport refuses **all** redirects, disables proxy inheritance, bounds successful/error responses and redacts exception strings. It retains the #548 per-node headers for synthetic interoperability research but does NOT make them usable for live Task dispatch.
- A dependency-free AST safety test locks down all five legacy route guards and verifies that they execute before graph operations. Hosted synthetic transport checks are also carried forward from #547.
- No feature flag, credential registration, live system change, Task approval, graph role mutation, Task claim, service restart, or production deployment is performed.

## Positive identity-only diagnostic (not machine authorization)

The new GET /api/fleet-node/identity path requires a valid registered machine token and responds only with the supplied, verified node ID, machine_identity_verified=true, legacy_task_authorized=false and task_admission=HOLD. Valid operator Basic credentials *alone* do not grant entry, and wrong/partial node tokens are rejected even alongside operator Basic. This endpoint never calls Neo4j, enumerates Tasks, grants capacity, claims work, or modifies runtime state. No production node token has been registered or exercised here. It is an isolated, source-only step toward proving each node's identity without misrepresenting the broader Task authority gate.

## Strict acceptance to enable machine work later

A **separate, reviewed implementation** must provide (1) independently approved per-node principal registration/rotation/revocation and exact signed model/runtime-capacity projection, (2) ID-only source-authoritative scoped Task observer with complete snapshot/policy and no cross-node disclosure, (3) Task-specific transactional claim/heartbeat/finalize CAS with exact owner/session/attempt/generation/lease, (4) authenticated, non-restorable resource COMMIT and external high-water acceptance, (5) off-host negatives for stolen/rotated/replayed tokens, model drift, zero slots, unauthorized admin, and cross-node Task access, (6) staged one-node physical positive and immutable rollback under separately granted operator authority.

**No existing positive per-node auth test can override those gates.** This stacked draft makes the legacy Task path reliably deny machine identity and safely transfers the transport fix. Release remains P0 NO-GO until the independent authority exists.

## One-shot agent-side identity diagnostic (latest research increment)

The fleet-node agent accepts the optional --identity-probe-only CLI switch. It invokes the identity-only GET once, prints nonsecret aggregate JSON, and exits code 2 on BOTH success and failure. It never enters the worker loop, polls Tasks, claims work, reports completion or loads a model. It sends a node credential only if FLEET_NODE_TOKEN and FLEET_NODE_ID exist and agree with the requested node identity; operator Basic credentials are never attached. An operating-system hostname fallback is explicitly rejected for this diagnostic, even when a token is present: the approved node ID must be configured by the credential owner.

The probe only permits an HTTPS root origin with normal TLS certificate checking, or literal loopback HTTP (127.0.0.1 or [::1]). It rejects nonloopback HTTP, userinfo, nonroot paths, queries, fragments and malformed ports before sending credentials. It also requires FLEET_NODE_IDENTITY_APPROVED_ORIGIN to be set separately to the exact configured target; an absent or differing pin fails before HTTP. This additional operator-owned origin pin prevents accidental node-token forwarding to an arbitrary valid-HTTPS URL. Credentialed requests separately reject redirects and disable environment proxies.

A positive server response must match the exact four-field identity-only reply, with task_admission=HOLD and legacy_task_authorized=false. Extra fields and any claim of Task admission fail closed.

Research-stage invocation only (staging origin deliberately non-real):

    python3 -m assistx.fleet_node_agent --identity-probe-only --assistx-url https://approved-staging.example.invalid

No token has been created, registered, distributed or read by this change. The five physical HTTP 401 findings remain unresolved until a separately approved machine-identity migration and scoped source-owned Task authority. All new positive and negative cases use socketless test fixtures.
