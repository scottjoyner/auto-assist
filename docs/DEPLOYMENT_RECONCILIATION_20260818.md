# Deployment reconciliation — 2026-08-18

## Why this pass exists

A substantial reconciliation package already exists on `main`. The purpose of this pass is **not** to replace it. The purpose is to identify what the repository can prove today versus what may still exist only on a working host or local agent worktree, especially Caddy edge exposure and the final operator deployment path.

## What is already in Git

The current `main` contains:

- `docker-compose.yml` plus production/reconciliation overlays;
- `compose.production.reconciled.yml`, with loopback-only host publication and explicit external networks/volumes;
- `deploy/reconciliation/system-inventory.yaml`;
- an operator-owned migration-ledger contract and cutover validator;
- LAN runtime mapping and Tailscale candidate discovery;
- LAN-first / Tailscale-fallback runtime access rules;
- offline verification, image-bundle, preflight, report, cutover and recovery documentation;
- explicit restrictions against public inference, automatic Tailscale admission, blanket host networking, and unbounded executor authority.

That means the core migration/control-plane work **did land**.

## Gaps this branch addresses

### 1. Caddy is not canonical in the repository

The existing reconciliation docs discuss private networking and runtime access, but there is no committed Caddyfile or machine-readable edge-exposure contract describing:

- where Caddy actually runs;
- which config path is authoritative;
- whether it is host-systemd, Docker, or Compose managed;
- public vs tailnet hostnames;
- TLS mode and certificate ownership;
- explicit upstreams and health checks;
- validate/reload/rollback commands;
- checksum of the last known-good config.

This branch adds `deploy/reconciliation/edge-exposure.example.yaml` as the contract that the local agent must fill from **captured working-host evidence**. It does not invent the missing live Caddy configuration.

### 2. Runtime reconciliation capture was fragmented

`scripts/reconciliation-capture-runtime.sh` provides one read-only collection step for Docker, networks, listeners, Tailscale, Caddy/systemd state, Compose renders, and repository identity. It does not restart or alter services.

The capture intentionally avoids raw Docker inspect output and certificate/key contents because those commonly contain secrets. Review every generated artifact before committing anything.

### 3. There was no single blessed operator entry point

`scripts/unified-deploy.sh` is now the top-level operator interface:

```bash
bash scripts/unified-deploy.sh doctor
bash scripts/unified-deploy.sh capture
bash scripts/unified-deploy.sh plan
bash scripts/unified-deploy.sh validate
bash scripts/unified-deploy.sh apply
bash scripts/unified-deploy.sh rollback-plan
```

`apply` is fail-closed. It requires:

1. the real reconciliation environment file;
2. the real migration ledger;
3. a passing `--require-cutover` validation;
4. a successful combined Compose render;
5. optional Caddy validation when `ASSISTX_CADDY_CONFIG` is supplied;
6. an explicit `ASSISTX_UNIFIED_DEPLOY_APPROVED=YES` acknowledgement.

The wrapper deliberately does not invent rollback commands; rollback remains operator-owned evidence in the migration ledger/final cutover packet.

## Canonical Compose order

For the reconciled production AssistX stack, this branch treats the existing order as canonical:

```text
docker-compose.yml
  -> compose.prod.yml
  -> compose.production.reconciled.yml
```

The final overlay keeps the API, Neo4j and router-facing ports private/loopback and relies on explicitly named external networks and volumes. Edge exposure should therefore be implemented outside those containers unless a later reviewed contract changes that boundary.

## Local-agent reconciliation checklist

The local agent should capture, but **not immediately overwrite**, the following from the working host:

- active Caddy config and SHA-256;
- `caddy version`, systemd/container ownership, and exact validate/reload command;
- active listeners for 80/443 and all AssistX/router/API service ports;
- Docker Compose projects and actual container names;
- Docker network names used by AssistX, router, Neo4j, Nextcloud/other adjacent services;
- named volumes and bind mounts that contain persistent state;
- `tailscale status --json` and `tailscale ip`;
- whether containers reach tailnet peers through ordinary host routing, a Tailscale sidecar, or host networking;
- MagicDNS behavior from inside the relevant containers;
- currently working public/tailnet hostnames and exact upstream targets;
- any local deploy/restart/rollback scripts not present in Git;
- exact repository/worktree/commit that produced the live containers.

Then compare those facts against:

- `compose.production.reconciled.yml`;
- `deploy/reconciliation/system-inventory.yaml`;
- `deploy/reconciliation/migration-state.yaml`;
- `deploy/reconciliation/edge-exposure.yaml`;
- this repository's scripts/docs.

Unknowns remain blockers. A working host configuration should be captured first and normalized second; do not replace working Caddy/Tailscale state with repository assumptions.

## Definition of done

Deployment reconciliation is complete only when:

- the working Caddy config is versioned or generated from a versioned template with secrets externalized;
- edge exposure YAML records the actual ownership/TLS/upstream model and checksums;
- one unified deploy `validate` pass succeeds on the target host;
- Caddy config validation succeeds before reload;
- Tailscale/LAN reachability matches the approved runtime-access contract;
- Compose service/network/volume names match actual persistent state;
- recovery/rollback commands are recorded and rehearsed;
- the final migration ledger passes the cutover gate;
- operator approval is explicit.

Until then, `main` describes a strong **candidate reconciled deployment**, not guaranteed proof of the exact currently running edge configuration.
