# Beelink recovery island — 2026-07-30

The Beelink is a physically separate recovery deployment for the reconciled AssistX fleet. It is intentionally isolated from ordinary execution and remains inert until a signed recovery workflow targets it.

## Authority model

```text
Normal operation
  AssistX/Neo4j authority on primary deployment
    -> signed recovery-island task
    -> Beelink recovery-island agent
    -> local, allowlisted deployment only

Total primary loss
  pre-staged Beelink deployment
    -> witness-signed or manual break-glass activation envelope
    -> local activation
```

The Beelink is not a second scheduler, router, model registry, health authority, or recovery brain. It does not reconstruct authority from probes and does not maintain a competing SQLite fleet database.

## Recovery-island states

- `empty`: no verified offline deployment bundle is staged;
- `prepared`: the exact checksum-pinned image bundle was loaded and the local Compose plan rendered successfully;
- `active`: a separately signed activation envelope with a fresh epoch and fence proof started the allowlisted services;
- `unhealthy`: activation completed but one or more private health checks failed, causing immediate automatic deactivation;
- `inactive`: the deployment was stopped while local volumes and evidence were preserved.

## Separate privileges

Two signatures are required for activation:

1. **Recovery runbook signature** — authorizes a typed operation such as `stage`, `verify`, `activate`, or `deactivate`.
2. **Recovery activation signature** — authorizes the Beelink to advertise a recovery deployment as active.

The activation envelope must contain:

```text
version
mode=activate
target_node_id
deployment
bundle_sha256
epoch
fence_proof
attestation key, issuance, expiry, nonce, and signature
```

Accepted fence-proof classes are:

- `assistx-lease:<id>` when the healthy authoritative control plane orders recovery;
- `witness:<id>` when an independent subnet witness grants exclusive takeover;
- `manual-break-glass:<id>` for an operator-approved disaster recovery event.

A new activation epoch must be greater than the last locally accepted epoch. Replayed signatures and stale epochs are rejected.

## Automatic and disaster recovery

### Routine automatic healing

When AssistX is healthy enough to diagnose and dispatch work, it may send a signed recovery-island task to the Beelink. This path can be completely automatic because the canonical control plane remains alive and can issue an exclusive recovery lease.

Typical sequence:

1. stage the latest image and backup bundle;
2. verify checksum, manifest, image load, and Compose render;
3. issue an `assistx-lease:*` activation envelope;
4. activate the recovery services;
5. verify AssistX and auto-router health;
6. verify runtime-projection convergence;
7. switch one test client;
8. enable Hermes only after the synthetic lifecycle gate;
9. either promote under operator policy or deactivate and repair the primary.

### Total control-plane loss

If the authoritative AssistX API is unavailable, the Beelink cannot obtain a new normal runbook. It may still activate a previously staged deployment through the local `activate-file` command, but only with a fresh witness or manual break-glass envelope.

Unattended total-loss takeover requires a separate witness capable of proving exclusivity. Without a witness, automatic activation remains blocked to prevent split brain.

## Isolation requirements

The recovery node should use:

- its own physical disk for deployment, state, images, and database backups;
- its own Docker/Podman project and named volumes;
- a dedicated non-login service account;
- rootless containers when practical;
- no SSH private key capable of logging into the primary fleet;
- one-way artifact delivery from the primary to the Beelink;
- subnet/Tailscale ACLs limited to the authoritative AssistX API, optional witness, approved inference paths, and operator access;
- no ordinary Hermes, code, shell, benchmark, or model-work capabilities;
- no router registration or physical-runtime admission authority.

The recovery agent polls only tasks requiring `recovery_island`. A task containing a generic command, model prompt, repository mutation, or ordinary fleet capability is rejected.

## Local paths

Recommended layout:

```text
/srv/assistx-recovery/deployment/         immutable reviewed Compose/config
/srv/assistx-recovery/packages/           offline Python wheel/source
/var/lib/assistx-recovery/bundles/        image and backup bundles
/var/lib/assistx-recovery/state/          nonce, epoch, prepared, active evidence
/var/lib/assistx-recovery/neo4j/           recovery Neo4j data
/var/lib/assistx-recovery/redis/           optional recovery Redis state
```

## Agent commands

Run the dedicated executor:

```bash
python -m assistx.recovery_island_agent \
  --node-id beelink-recovery \
  --state-dir /var/lib/assistx-recovery/state \
  loop \
  --assistx-url http://<primary-assistx>:8000
```

Inspect local state:

```bash
python -m assistx.recovery_island_agent \
  --node-id beelink-recovery \
  --state-dir /var/lib/assistx-recovery/state \
  status assistx
```

Execute an operator-reviewed signed runbook file:

```bash
python -m assistx.recovery_island_agent \
  --node-id beelink-recovery \
  --state-dir /var/lib/assistx-recovery/state \
  execute-file /var/lib/assistx-recovery/inbox/runbook.json
```

Break-glass activation after complete primary loss:

```bash
python -m assistx.recovery_island_agent \
  --node-id beelink-recovery \
  --state-dir /var/lib/assistx-recovery/state \
  activate-file assistx /var/lib/assistx-recovery/inbox/activation.json
```

## Required evidence before relying on the island

- Beelink physical node identity and node-token registration;
- reviewed recovery deployment configuration checksum;
- image bundle and manifest checksums;
- current Neo4j backup checksum and isolated restore result;
- successful offline `docker load` and Compose render;
- successful prepared-state inspection;
- signed activation with an `assistx-lease:*` proof;
- private health checks and runtime-projection convergence;
- automatic rollback after an intentionally failed health check;
- stale epoch and replay rejection;
- deactivation preserving volumes and evidence;
- witness or manual break-glass rehearsal for total control-plane loss;
- proof that ordinary tasks cannot be claimed or executed by the Beelink agent.

The Beelink should not be considered a production recovery target until all evidence is recorded in the final cutover contract.
