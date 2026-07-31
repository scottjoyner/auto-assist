# Beelink recovery island — 2026-07-30

The Beelink is a physically separate recovery deployment for the reconciled AssistX fleet. It is intentionally isolated from ordinary execution and remains inert until a signed recovery workflow targets it.

## Authority model

```text
Normal operation
  primary AssistX/Neo4j authority
    -> authenticated recovery-island request
    -> fingerprinted proposal and approval policy
    -> Neo4j-fenced recovery-island dispatcher
    -> target-pinned canonical recovery task
    -> dedicated Beelink host agent
    -> local allowlisted deployment only

Total primary loss
  pre-staged Beelink deployment
    -> independent witness or manual break-glass activation envelope
    -> local shadow activation
```

The Beelink is not a second scheduler, router registry, model registry, health authority, or recovery brain. It does not reconstruct authority from probes and does not maintain a competing fleet database.

## Three recovery tiers

```text
assistx-shadow
  neo4j-restore, neo4j, redis, assistx-api, auto-router

assistx-executor
  assistx-worker

executor profile
  hermes-adapter
```

`assistx-shadow` is the only tier recommended for automatic activation. Its API runs with `ASSISTX_RECOVERY_SHADOW_MODE=true`; schema and read/health endpoints are available, while normal startup loops that could mutate restored state or execute restored work are disabled.

`assistx-executor` requires a separate promotion action after shadow health and runtime projection convergence. Hermes remains a third explicit activation after a fenced synthetic task succeeds.

## Recovery-island states

- `empty`: no verified offline deployment bundle is staged;
- `prepared`: the exact checksum-pinned image bundle was loaded, every manifest image ID was inspected, and the local Compose plan rendered successfully;
- `active`: a separately signed activation envelope with a fresh epoch and fence proof started the allowlisted services;
- `unhealthy`: activation completed but one or more private health checks failed, causing immediate automatic deactivation;
- `inactive`: the deployment was stopped while local volumes, nonce evidence, and the highest accepted activation epoch were preserved.

Activation epochs survive deactivation. A failed or rolled-back activation consumes its epoch and cannot be replayed.

## Separate privileges

The system uses four distinct credentials:

1. **Recovery request token** — allows an authenticated agent to request narrow policy evaluation; it cannot sign a runbook.
2. **Recovery-island runbook key** — authorizes only `stage`, `verify`, `activate`, or `deactivate` against a static deployment allowlist.
3. **Recovery-island activation key** — separately authorizes a deployment to become active.
4. **Beelink node token** — protects polling, claim, heartbeat, and completion through the canonical AssistX `recovery` lane.

Ordinary recovery runbook keys are not accepted by the Beelink island agent. Signing keys remain in the fenced primary worker; the API can create proposals but cannot sign a Beelink runbook.

The activation envelope contains:

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

- `assistx-lease:<controller>:<fencing-token>:<proposal-id>`;
- `witness:<exclusive-lease-id>`;
- `manual-break-glass:<operator-change-id>`.

Replayed signatures, stale epochs, wrong nodes, wrong deployments, wrong bundle checksums, expired envelopes, and missing fence proofs are rejected.

## Agent request and approval policy

Agents request recovery through:

```text
POST /api/fleet/recovery-island/requests
X-Recovery-Island-Request-Token: <request token>
```

The request is deduplicated by its canonical proposal fingerprint. Auto-approval requires all of:

- request token match;
- authenticated actor in `ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTORS`;
- action in `ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTIONS`;
- for activation, `ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_ENABLED=true`;
- deployment in `ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_DEPLOYMENTS`.

The recommended automatic activation allowlist contains only `assistx-shadow`.

The existing generic recovery execution endpoint is fenced. For an island proposal it returns queued dispatcher state and cannot convert the proposal into an ordinary recovery runbook. Non-island recovery retains its existing behavior.

## Routine automatic healing

A safe sequence is:

1. request and approve `stage assistx-shadow`;
2. verify bundle checksum, manifest, loaded image IDs, and Compose render;
3. request and approve `activate assistx-shadow`;
4. issue an `assistx-lease:*` activation envelope from the fenced dispatcher;
5. verify AssistX health and `/api/fleet/recovery-island/shadow-status`;
6. verify auto-router health and runtime-projection convergence;
7. separately stage and activate `assistx-executor`;
8. execute one fenced synthetic task;
9. start the Hermes `executor` profile only after the synthetic lifecycle gate;
10. promote under operator policy or deactivate the island and repair the primary.

## Total control-plane loss

If the primary AssistX API is unavailable, the Beelink cannot obtain a new normal runbook. It may activate a previously staged shadow through the local hardened entrypoint, but only with a fresh witness or manual break-glass envelope.

```bash
sudo -u assistx-recovery \
  /srv/assistx-recovery/venv/bin/python \
  -m assistx.recovery_island_agent_hardened \
  activate-file assistx-shadow \
  /var/lib/assistx-recovery/inbox/activation.json
```

Unattended total-loss takeover requires an independent witness capable of proving exclusivity. Without a witness, automatic activation remains blocked to prevent split brain. Executor and Hermes promotion remain separate even after shadow activation.

## Isolation requirements

The recovery node uses:

- its own physical disk for deployment, state, images, and database backups;
- its own Compose project and local volumes;
- a dedicated non-login service account;
- rootless containers when practical;
- no SSH private key capable of logging into the primary fleet;
- one-way artifact delivery from the primary or operator workstation;
- subnet/Tailscale ACLs limited to the primary AssistX API, optional witness, approved inference paths, DNS/NTP, and operator access;
- no ordinary shell, model, browser, benchmark, repository, or routing capability in the host agent;
- key files mode `0600` or stricter;
- no credentials in systemd process arguments;
- read-only deployment/config/package trees and a writable state tree only.

The host agent polls the canonical `recovery` capability so existing AssistX node-token enforcement applies. It then applies a narrower second filter and rejects every task that lacks a valid `recovery_island_runbook` targeted to the exact Beelink identity.

## File-backed host configuration

```text
/etc/assistx-recovery/recovery-island.env
/etc/assistx-recovery/deployments.json
/etc/assistx-recovery/runbook-verify-keys.json
/etc/assistx-recovery/activation-verify-keys.json
/srv/assistx-recovery/deployment/recovery-island.env
/srv/assistx-recovery/deployment/recovery-stack.env
```

The production systemd entrypoint is:

```text
python -m assistx.recovery_island_agent_hardened loop
```

The Compose interpolation file is loaded into every `config`, `up`, and `stop` subprocess. Activation uses `--no-build --pull never`.

## Required evidence before production reliance

- Beelink physical identity and node-token registration;
- separate request, runbook, and activation credentials;
- key-file permission evidence and process-argument inspection;
- reviewed shadow/executor deployment checksums;
- image bundle, manifest, and every loaded image-ID verification;
- current Neo4j backup checksum and isolated restore result;
- successful offline stage for both shadow and executor entries;
- proof restored READY work remains inert in shadow mode;
- signed shadow activation with an AssistX lease;
- private health checks and runtime-projection convergence;
- automatic rollback after an intentionally failed health check;
- stale epoch rejection after deactivation;
- nonce replay, tamper, wrong target, and wrong bundle rejection;
- separate executor activation and worker health;
- fenced synthetic task before Hermes starts;
- deactivation preserving data and activation epoch evidence;
- witness or manual break-glass shadow rehearsal for total primary loss;
- proof that ordinary tasks and ordinary recovery runbooks cannot execute on the host agent.

The Beelink is not a production recovery target until this evidence is recorded in the final cutover contract.
