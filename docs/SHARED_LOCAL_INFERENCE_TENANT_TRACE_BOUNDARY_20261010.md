# Shared local inference tracing: tenant isolation and source authority

**2026-10-10 | Draft research / no production change, no DB migration.**

## Observed omission

Phone-local request history and a W3C traceparent do not prove which user, model instance, physical node, or retry executed an inference. Direct LM Studio and llama.cpp calls bypass the AssistX gateway. The experimental fleet-trace ingress formerly filtered trace reads by node only, not tenant and not producer. Two users sharing a model node could therefore see metadata from each other's traces. Auto-router also accepts a user-selected X-Trace-ID. None of these client IDs is authority.

Read-only network observation: LM Studio listens at 0.0.0.0:1234 on x1-370. Actual WAN/LAN/Tailnet reachability is **not** established by the bind address.

## Implemented isolated receiver hardening

- The authenticated server-side policy binds each writer to a single write_tenant, and each reader to explicit read_tenants, nodes, and producers. No tenant identity from user-controlled headers or event JSON.
- Storage records policy-derived tenant_scope and producer with each event. Primary key (tenant_scope, event_id) avoids cross-tenant collisions when two clients reuse event IDs.
- Every trace query intersects tenant + authenticated writer/owner principal + node + producer + trace ID. Organization-wide tenant rights alone do not expose one person's events to another. A shared node does not confer cross-user access.
- Old unscoped SQLite tables are rejected as requiring a separately approved migration. The code does not silently backfill untrusted historical ownership or modify a pre-existing schema.
- A valid W3C traceparent or HTTP 200 is still correlation only, not proof of admission, source execution, or ownership. The receiver still reports source_attested=false.
- Static tagged-producer identity cannot distinguish two end users behind one shared service. This feature must remain NO-GO for public shared inference until a gateway issues and verifies per-request delegated owner identities.

## Required unified end-to-end lineage

1. At authenticated ingress, establish subject, tenant, capabilities and server-minted root request ID from trusted credentials. Client traceparent is observational only.
2. Record child spans at gateway admission, router selection, endpoint allocation, model residency, queue, first token, stream completion, cancellation, retry/failover, tokens and errors. Unknown means unobserved, never success.
3. Enforce owner scope for shared local endpoint access. Unmediated LM Studio/llama.cpp requests have no trusted per-user identity and must not be advertised as fully traced.
4. Keep prompts, response bodies, coordinates, bearer tokens, arbitrary headers and attachments out of default shared trace payloads.
5. Offline replay must be bounded, idempotent and owner-authenticated, with event sequence, occurred and received timestamps, file custody and storage pressure enforcement. Lost SSE delivery is not model completion.
6. Tenant-filter historical search AND live subscriptions. Cross-tenant operator access requires explicit capability and audit, not only node or trace ID.
7. Choose permanent index or graph only after physical multi-user workload, storage, recovery and deletion tests. Do not equate a local digest with signed custody.

## Test and rollout boundaries

34 focused ingress tests passed on disposable SQLite: two tenants with identical W3C trace/event IDs on the same node, node-only denial, producer-only denial, untrusted owner header denial, missing policy denial, legacy schema refusal and same-tenant event takeover refusal. Additional source-contract tests and live multi-user model traffic remain release gates.

This patch is not deployed. It does not configure Tailscale Serve, alter inference endpoints, enable OpenTelemetry, write production traces, rotate secrets, migrate data or authorize Agent Auto. A separate owner decision is needed before any live installation.

### Multi-user trace pipeline

iPhone/agent -> authenticated inference broker (trusted tenant/subject)
-> AssistX/auto-router (server request ID, trace + span lineage)
-> local model endpoint (actual instance and streaming lifecycle)
-> tenant-scoped metadata receipt with authenticated producer
-> durable bounded ingest and authorised history UI

W3C trace IDs, local host names and provider inventory are not identity proof.
