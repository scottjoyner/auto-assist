# AssistX Fleet Secrets and Identity Plan

Status: design/build phase. No credential rotation is authorized by this plan.

## Current security posture

Credentials may have appeared in historical runbooks, shell commands, environment files, container environments, or recovery bundles. Treat those locations as sensitive. Until Scott explicitly authorizes rotation, this project performs inventory and control-plane design only.

Rules:

- Never copy secret values into Neo4j, AssistX tasks, dashboard JSON, Markdown, Git, logs, or chat.
- Never use a secret value as a node identifier, deduplication key, or checksum exposed to operators.
- New records contain references and metadata only: owner, purpose, scope, storage reference, status, and rotation state.
- Existing credentials remain untouched during this phase.
- Any future rotation must be staged, verified against dependent services, and reversible where possible.

## Target architecture

```text
Tailscale node identity (physical truth)
  ├── HardwareProfile
  ├── NetworkAddress (tailnet/LAN, current + historical)
  ├── ServiceInstance (what runs there)
  ├── RuntimeCapability (what can receive work)
  ├── StorageMount
  ├── AgentSession / provider identity (logical layer)
  └── SecretBinding (metadata-only reference)
          └── external encrypted secret store
```

AssistX is the policy and audit broker, not the secret database. A future broker should resolve a `secret_ref` only at the final service boundary, using a short-lived, least-privilege identity. Secret values must not enter Neo4j or the AssistX event/task graph.

## Canonical fleet model

`FleetNodeState` is one current physical record per Tailscale node ID. Hostnames and IPs are mutable attributes, not identity keys. Historical names and addresses attach as dated observations.

Recommended fields:

- `tailscale_node_id` — immutable physical identity
- `hostname`, `magic_dns`, `tailscale_ips` — current network observation
- `online`, `last_seen`, `observed_at` — freshness
- `os`, `architecture`, `hardware_profile_id`
- `role` — hub, compute, storage, recovery, client, phone, etc.
- `provenance` — source and observation ID

Separate labels/entities:

- `HardwareProfile`
- `ServiceInstance`
- `RuntimeCapability`
- `StorageMount`
- `AgentSession`
- `ModelEndpoint`
- `SecretBinding`
- `NetworkObservation`

Do not merge provider IDs, agent names, Docker service names, or AssistX logical nodes into `FleetNodeState`.

## SecretBinding metadata contract

A `SecretBinding` may contain:

- `secret_ref` — opaque backend reference, never the value
- `name` — non-sensitive logical name
- `owner` — service or operator owner
- `consumer` — node/service identity
- `scope` — exact operation or endpoint scope
- `backend` — Vaultwarden, Infisical, SOPS/age, OS keyring, etc.
- `status` — planned, discovered, staged, active, retired
- `exposure_state` — unknown, suspected, confirmed, remediated
- `last_verified_at`
- `provenance_ref`

A binding must not contain passwords, tokens, private keys, recovery phrases, or raw environment values.

## Build phases

1. **Inventory-only (current)**
   - Enumerate secret-bearing files, environment surfaces, container mounts, and service consumers without printing values.
   - Classify exposure risk and provenance.
   - Add metadata-only `SecretBinding` records.
   - Fix future logging/redaction gaps.

2. **Backend decision**
   - Compare Vaultwarden, Infisical, and SOPS/age against fleet/offline-recovery requirements.
   - Require encrypted-at-rest storage, audit trail, scoped access, rotation API, export/recovery procedure, and no plaintext secret sync.

3. **AssistX broker**
   - Add read-only inventory endpoints first.
   - Add policy-checked `resolve(secret_ref, consumer, purpose)` only after backend selection.
   - Issue short-lived leases; never return secrets through task history, WebSocket events, or dashboard responses.
   - Log metadata only: requester, consumer, reference, scope, decision, timestamp.

4. **Fleet integration**
   - Bind credentials to `ServiceInstance` and canonical Tailscale node ID.
   - Distinguish node-local secrets, hub secrets, service secrets, recovery secrets, and human credentials.
   - Add dependency edges so a future rotation can identify affected services before changing anything.

5. **Migration and rotation (separate authorization)**
   - Migrate one dependency at a time.
   - Verify service health and access paths.
   - Rotate only after explicit approval.
   - Preserve a recovery checkpoint and auditable result.

## Immediate next build slices

- Complete a metadata-only credential-surface inventory.
- Add tests preventing secret values from entering `SecretBinding` or dashboard/task payloads.
- Reconcile the two SSH probe gaps through an authorized access path.
- Extend the fleet graph schema with stable Tailscale identity and observation provenance before adding more hardware data.
- Do not rotate, delete, or rewrite credentials in this phase.
