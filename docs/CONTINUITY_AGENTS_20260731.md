# AssistX continuity agents — July 31, 2026

This document covers the two lightweight processes that connect the authoritative primary AssistX controller, the Beelink continuity plane, and existing LM Studio or verification nodes.

Neither process is a general autonomous deployment agent. Neither accepts arbitrary shell, script, SSH, browser, repository, package-install, model-load, or model-unload work.

## Projection replicator

`assistx.continuity_replicator` runs beside the authoritative primary AssistX API. It periodically copies only fresh bounded policy documents to the Beelink continuity API.

Default documents:

- required signed runtime projection;
- optional bounded context/KV metadata projection.

The replicator:

- reads the source from the local authoritative AssistX API;
- authenticates to the source with Basic Auth whose password is stored in a separate mode-0600 file;
- tries the Beelink LAN address first and its Tailscale address second;
- verifies the expected continuity cluster and controller identity;
- binds every write to the Beelink's current monotonic recovery epoch;
- validates projection expiry, identity completeness, model instances, access paths, checksum, and signature presence;
- rejects raw prompt, message, token-ID, or raw-context material;
- stores documents with short TTLs;
- refreshes unchanged documents before half of their lease expires;
- fails the iteration when a required document cannot be validated or replicated.

The replicator does not copy Neo4j, approve access paths, infer capacity, discover nodes, or promote the Beelink. It copies an already approved short-lived projection.

### Configuration

Template:

```text
deploy/reconciliation/continuity-replicator.env.example
```

Service:

```text
deploy/reconciliation/systemd/assistx-continuity-replicator.service
```

Required secret files:

```text
/etc/assistx-continuity/continuity-token
/etc/assistx-continuity/source-basic-password
```

Both files must be mode `0600` or stricter.

## Bounded continuity node worker

`assistx.continuity_node_agent` runs on existing fleet nodes, including LM Studio nodes. It is a stdlib-only pull worker for narrow recovery and verification work.

Default task handlers:

- `runtime_probe`: GET the configured local OpenAI-compatible `/v1/models` endpoint;
- `http_probe`: GET an exact private allowlisted service URL and return only status, response size, and response digest;
- `artifact_checksum`: SHA-256 one file below an explicitly allowlisted local root;
- `backup_verify`: verify an artifact set below an allowlisted root and require named databases such as `system` and `neo4j`.

The worker:

- tries the Beelink LAN continuity endpoint first and its Tailscale endpoint second;
- checks the expected cluster and controller identity;
- persists the highest accepted recovery epoch locally;
- rejects any controller response with a lower epoch;
- reports capabilities and current memory headroom;
- claims only tasks whose required capabilities it advertises;
- completes tasks with the exact claim token;
- returns a structured failure for every non-allowlisted task kind;
- never returns a health-probe response body, only a digest and byte count;
- confines file operations to configured artifact roots;
- rejects public HTTP targets even when a URL prefix is mistakenly allowlisted.

The worker has no generic command runner. A task named `script`, `shell`, `ssh`, `deploy`, `repository`, or any other unregistered kind fails before execution.

### Configuration

Template:

```text
deploy/reconciliation/continuity-node.env.example
```

Service:

```text
deploy/reconciliation/systemd/assistx-continuity-node.service
```

Required token file:

```text
/etc/assistx-continuity/continuity-token
```

The systemd service runs with a 192 MiB hard memory limit, 25% CPU quota, no Linux capabilities, a read-only operating-system view, no home access, private devices and temporary directory, and only `/var/lib/assistx-continuity` writable.

Artifact roots are read-only under `ProtectSystem=strict`. Keep them outside protected home directories and narrow the environment variable to the exact backup/artifact directories needed by that node.

## Installation boundary

These templates assume a dedicated unprivileged service account:

```text
assistx-continuity
```

The reviewed code or wheel and virtual environment live under:

```text
/srv/assistx-continuity
```

Persistent epoch state lives under:

```text
/var/lib/assistx-continuity
```

Configuration and secret files live under:

```text
/etc/assistx-continuity
```

The units do not install software, clone repositories, modify firewall rules, change Tailscale settings, or create the service account. Those remain explicit operator deployment steps.

## Failure behavior

### Primary controller down

The projection replicator stops refreshing documents. Existing Beelink projections expire after their bounded TTL. The continuity router fails closed rather than extending approval indefinitely.

### Beelink LAN path down

Both agents try the configured Tailscale path. The second path is accepted only when it reports the same expected cluster/controller identity.

### Beelink restarted

The continuity node worker compares the reported recovery epoch with its locally persisted highest epoch. A restored or compromised controller presenting a lower epoch is rejected.

### Node worker restarted during a task

The continuity claim expires. The Beelink may later offer the task to another capable node. Final completion still requires the claim token and current epoch.

### Malicious or accidental broad task

The node agent records a failed outcome. It does not interpret payload fields as commands.

## Required rehearsal

Before deployment approval, prove:

1. Primary-to-Beelink runtime projection replication over LAN.
2. Automatic fallback to Tailscale with the same controller identity.
3. Projection expiry and router fail-closed behavior when both paths stop.
4. Node heartbeat and runtime probe from at least two LM Studio nodes.
5. Backup verification on a non-Beelink node.
6. Capability filtering and claim expiry/reassignment.
7. Script, shell, SSH, and path-escape rejection.
8. Epoch rollback rejection after the Beelink is restored from old state.
9. Service-account memory and CPU limits under sustained polling.
10. No credentials visible in `ps`, systemd command lines, or journal output.

Automatic SSH deployment is intentionally deferred. A later deployment executor must use a separate identity, signed immutable manifest, command allowlist, single-worktree scope, post-deployment checks, and automatic rollback rather than extending these continuity agents.
