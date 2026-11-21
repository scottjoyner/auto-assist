# OpenCode bridge router-admin credential cutover

**Scope:** source-only changes to scripts/opencode-bridge/sync_configs_to_assistx.py, independently review before enabling or deploying. No node enrollment, router change or Task dispatch authorization.

## Confirmed source problem

The existing bridge unconditionally invoked POST /admin/live-models/refresh with empty user/password, so its _post() attached no authentication. That optional admin route is currently unmounted (404 in deployed router), but router PR #123 adds require_admin to its handler. This legacy script must not be a reason to keep a mutation route unauthenticated.

## Source-only correction

- Ordinary bridge sync still registers/probes AssistX ModelEndpoints under the existing operator Basic credential, but **router refresh is skipped by default**. No router admin call without the explicit --refresh-router flag.
- Require independent AUTO_ROUTER_ADMIN_TOKEN before any registration or probe if privileged refresh is requested; reject malformed router origins and URLs containing userinfo/path/query/fragment.
- HTTP is permitted only for localhost/loopback or a literal shared-address-space Tailnet IP in 100.64.0.0/10. This range ALONE does not prove active Tailscale transport: the operator must independently verify peer and route. Other origins require verified HTTPS with explicit port. The destination is operator-supplied, and must be reviewed against the approved router.
- Send one bounded, credential-bearing POST with X-Admin-Token, not shared node/AssistX Basic credentials. Disable proxy inheritance and all redirects; do not echo token, remote error body or redirect destination.
- Refused/failed refresh produces nonzero CLI exit with sanitized error. Source validation is not proof of deployed router compatibility: the present live optional path still returns 404.

The --dry-run flag never invokes the network. For a future owner-approved window, use the separate --refresh-router flag with AUTO_ROUTER_ADMIN_TOKEN present only in the process environment (not pasted into shell arguments). Note that non-dry-run bridge registration/probing already mutates AssistX; it is not transactionally coupled to the router refresh.

## Required staged acceptance

1. Audit all router admin callers, including AssistX Control Room catalog and this bridge. Stage authenticated AssistX catalog PR #288 first; verify the strict legacy 404 fallback.
2. Test router PR #41 inherited and PR #123 optional admin authentication in isolated pinned images: anonymous 401 and properly authenticated behavior. Do not enable optional refresh routes just to satisfy this bridge.
3. Before any live privileged refresh, verify the exact configured Tailnet peer identity, authenticated ingress, and rollback. Use an isolated fixture before an owner-approved live request.
4. Independently recheck router runtime route identities and caller behavior after staged release. Keep machine-node credential separation, signed Task capacity, Neo4j custody, and production Task dispatch HOLD.

## Test limits

Standalone tests use only mocked urllib transports and synthetic data, exercising missing-token preflight, dry run, default skip, explicit opt-in, admin-header isolation, restricted URL transport, redirect/HTTP failures, redaction, and disabled proxy forwarding. No live network, model, Task, Neo4j, router service or credential access.

**Production remains NO-GO for automatic admin cutover or Task dispatch.**
