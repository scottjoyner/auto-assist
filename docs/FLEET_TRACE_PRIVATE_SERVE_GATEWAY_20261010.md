# Private fleet tracing ingress — deployment contract (2026-10-10)

**Status: draft pilot; no production Serve/Funnel routes, services, policies or storage modified.** Stacked on AssistX PR #271 (fleet trace schema and measured inventory). This is an authenticated metadata receiver, not an OTLP collector, a remote MCP execution endpoint, or a canonical signed custody store.

## Boundary choice

Private: enrolled Tailscale clients -> Tailscale Serve HTTPS :8447 -> 127.0.0.1:8794 -> isolated metadata-only FastAPI receiver -> local owner-only SQLite. Tailscale grants restrict connectivity, and the receiver requires BOTH trusted Serve mode and a registered login or application capability with a concrete node/producer scope.

Public: external SaaS or remote MCP clients cannot claim Tailscale Serve login identity through Funnel. A future separate public broker may use Funnel, but must independently implement OAuth2.1/OIDC audience and scopes, rate limiting, replay protections and restricted read-only MCP tools. Public broker is NOT implemented. Do not expose shell tools, Fleet Commander execution or raw trace content by changing this Serve configuration.

## Live footprint observed on x1-370

Tailscale v1.102.4. Private AssistX Serve at :8443 with bounded API paths. Existing PUBLIC Funnel at :8445 forwards to home voice bridge :8781, and PUBLIC Funnel at :8446 forwards to local service :8123. These pre-existing surfaces were inspected only; their own access controls deserve separate review. Proposed private :8447 is not configured. The read-only audit reported no conflict, but the backend service is not yet installed. The setup script never uses tailscale funnel or resets any existing Serve routes.

## Authorization

- User principals: trusted Tailscale-User-Login inserted by Serve, then explicit FLEET_TRACE_AUTH_POLICY_JSON entry such as login:operator@example.com with read/write and permitted nodes/producers. Arbitrary members of the tailnet have no access.
- Tagged service nodes: no user login header is expected. Configure Serve to accept nez.ninja/cap/fleet-trace-write and nez.ninja/cap/fleet-trace-read app capabilities, which Tailscale issues from the admin grant policy. The capability must contain an explicitly registered principal such as tag:trace-worker; the receiver independently verifies write/read, allowed node IDs and producer type. It cannot infer node identity from the caller's JSON.
- Never trust forwarded headers on arbitrary LAN sockets. Run the backend on 127.0.0.1 only under a dedicated local Unix UID, with the trace DB directory mode 0700, database mode 0600. If other untrusted local users share the host, prefer tsnet WhoIs or a private socket mediator for stronger protection.
- Trace IDs and response headers are correlations, never authentication/authority; Agent Auto approval remains a separate signed governance boundary.

## Event and API contract

GET /healthz: minimal liveness, not admission.
POST /v1/events: one assistx.fleet.trace.v1 allowlisted JSON envelope, <=8192 request bytes, <=4096 validated envelope bytes, strict W3C traceparent matching event trace ID and nonzero span ID. Returns idempotent local receipt (first event HTTP 201, exact replay HTTP 200, conflicting duplicate HTTP 409), SHA-256 digest, and source_attested=false. Unknown or sensitive raw fields are rejected.
GET /v1/traces/{trace_id}/events: read permission and node-scoped SQL filter; at most 100 recent rows; warns that history may be partial.

SQLite uses synchronous FULL, denies after 50,000 rows rather than silently evicting; this is an explicitly bounded local pilot, not a recommended permanent fleet storage backend. Model/system prompts, credentials, GPS coordinates, command output, and arbitrary telemetry attributes are not accepted. The packaged receiver currently uses a copy of the research metadata validator; release reviews must keep it equal to scripts/trace_metadata_contract.py.

## Approved setup path (NOT performed)

1. Provision dedicated fleet-trace Unix account, approved Python environment under /opt/auto-assist, local /var/lib/fleet-trace directory mode 0700, and an owner-only environment file.
2. Configure FLEET_TRACE_TRUSTED_SERVE=1, FLEET_TRACE_DB_PATH=/var/lib/fleet-trace/events.sqlite and explicit FLEET_TRACE_AUTH_POLICY_JSON. The example below is illustrative, not authorization for any actual user.
3. Review and install deploy/fleet-trace-ingress.service.example. Keep its bind 127.0.0.1:8794 and verify no wildcard LAN listener.
4. Approve the Tailscale grants for ONE expected producer/reader, separately from network access policy. Do not grant everyone trace-write.
5. Run FLEET_TRACE_SERVE_PORT=8447 FLEET_TRACE_LOCAL_PORT=8794 bash scripts/configure_private_fleet_trace_serve.sh with no arguments first. It refuses localhost listener, Serve collision and public Funnel issues. After explicit operator change approval, use FLEET_TRACE_APPLY_APPROVED=YES with --apply. This creates ONLY a new private Serve port; no pre-existing paths modified.
6. From an enrolled scoped node, send one harmless heartbeat with PYTHONPATH=src python3 scripts/send_fleet_trace_event.py --endpoint https://HOST.tailnet.ts.net:8447 --node-id xwing --producer worker. Verify the receipt, repeat idempotency, and independently authorized readback. Do not claim physical execution from a locally received event.

## Deferred release gates

- Confirm app-capability grants are configured to bind a real caller to the proposed scope; production Tailnet policy was not modified or tested.
- At least two distinct physical nodes and runtimes must supply authenticated sample traces. Cross-host clock skew, missing spans, retry behavior and offline replay are NOT verified yet.
- Storage benchmark, backups, quota alerts, signed source receipts, independent custody attestations, archival choice, privacy audit and long-term retention are open.
- This is not native OpenTelemetry OTLP/HTTP /v1/traces yet. Add an OTLP Collector AFTER the identity gate and attribute allowlist; do not stand up an unauthenticated collector on a public port.
- ChatGPT-style external integration needs an independently authenticated public broker with scoped MCP methods, not raw Funnel-to-fleet. The existing Fleet Commander plugin's private connectivity is not assumed to grant other services authority.
- No new service was started, no current Funnel was changed and no production trace was ingested by this PR.

## Cross-system lineage acceptance

The sample producer now carries the incoming W3C parent span ID into the new event parent_span_id, while assigning a fresh child span ID. A synthetic two-producer/two-node authenticated gateway test shows a shared trace with zero missing parents to a reader authorized for BOTH nodes; a reader scoped only to xwing receives its own child event but is correctly unable to prove the parent. **34 focused tests passed**, including this negative visibility case. This is a synthetic contract witness, not evidence that two physical nodes have submitted production traces.
