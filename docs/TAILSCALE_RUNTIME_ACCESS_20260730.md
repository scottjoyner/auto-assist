# Tailscale runtime access and LAN preference

This document defines private-network discovery and path selection for the reconciliation deployment.

## Required behavior

A physical runtime may remain useful when its machine leaves the local network. AssistX therefore records multiple private access paths for the same runtime:

1. an operator-confirmed RFC1918 LAN URL, when available;
2. an existing operator-selected or host-gateway URL;
3. a Tailscale `100.64.0.0/10` URL or approved MagicDNS URL.

The router probes these approved paths in order. It prefers the LAN path and falls back to Tailscale. The paths do not create separate runtime, model, or capacity records.

```text
PhysicalNode xwing
  └─ RuntimeInstance lmstudio-xwing-1234
       ├─ AccessPath lan:       http://192.168.1.51:1234/v1
       ├─ AccessPath tailscale: http://100.90.80.70:1234/v1
       └─ LoadedModelInstance qwen-...

parallel_slots = 1 across every access path
```

## Discovery authority

Tailscale discovery is owned by AssistX-side observation tooling, not by `auto-router`.

Run:

```bash
cp deploy/reconciliation/lan-runtime-map.example.json \
  deploy/reconciliation/lan-runtime-map.json
# Replace every example LAN address.

make reconciliation-discover-tailnet
```

The command executes `scripts/reconciliation-discover-tailnet.py`, which reads `tailscale status --json` and writes:

```text
artifacts/reconciliation-tailnet-candidates.json
artifacts/reconciliation-tailnet-candidates.json.sha256
```

To use the preflight snapshot rather than querying the live daemon again:

```bash
make reconciliation-discover-tailnet \
  RECON_TAILSCALE_STATUS=artifacts/reconciliation-preflight/<stamp>/tailscale-status-json.txt
```

The direct `tailscale status --json` file must contain JSON only. The preflight wrapper currently adds command headers, so extract the JSON body first when using that evidence file as `RECON_TAILSCALE_STATUS`.

## Candidate-only rule

A Tailscale peer is only a reachability candidate. Discovery does not prove:

- that the peer runs LM Studio or another inference server;
- that a model is loaded;
- that the discovered API belongs to the expected physical process;
- that the runtime has any available slots;
- that the node is approved for workload execution.

Every discovered record is marked `candidate_only`. Before admission, AssistX still requires:

- physical node and runtime identity;
- official LM Studio process evidence when applicable;
- loaded-model identity and quantization;
- explicit parallel slot capacity;
- direct completion health;
- routed completion health;
- cancellation and concurrency evidence;
- an operator-approved freshness window.

Unknown identity or capacity remains unroutable.

## Docker reachability

The reconciliation router remains on a normal Docker bridge network. This preserves loopback-only host publication while allowing outbound traffic to use the host's LAN and Tailscale routes.

Before cutover, prove from inside `auto-router-reconciliation` that each admitted path is reachable:

```bash
docker exec auto-router-reconciliation python - <<'PY'
import os
import httpx

for name in ("RECONCILIATION_LAN_BASE_URL", "RECONCILIATION_TAILSCALE_BASE_URL"):
    base = os.getenv(name, "").rstrip("/")
    if not base:
        print(name, "not configured")
        continue
    try:
        response = httpx.get(f"{base}/models", timeout=3)
        print(name, base, response.status_code)
    except Exception as exc:
        print(name, base, "unreachable", type(exc).__name__, str(exc))
PY
```

Also inspect the authenticated router state:

```bash
curl -fsS \
  -H "X-Admin-Token: $AUTO_ROUTER_ADMIN_TOKEN" \
  http://127.0.0.1:18088/admin/admission | jq
```

The selected path must identify the expected `runtime_instance_id` and transport.

If MagicDNS does not resolve inside Docker, use the Tailscale IP from the host-side status snapshot. Do not switch the whole stack to host networking merely to obtain MagicDNS; doing so weakens port and network isolation.

## Movement and failover

When a machine moves to another network:

1. its LAN path becomes unreachable;
2. its Tailscale IP remains an approved path if the peer is online;
3. the router's short-lived path cache expires;
4. the router probes LAN first, then selects Tailscale;
5. admission continues to use the same physical runtime slot pool.

When the machine returns to the LAN, the next path refresh probes the LAN URL first and restores local routing without changing runtime identity.

## Cutover evidence

The ledger and final report should record:

- Tailscale status snapshot and checksum;
- candidate discovery artifact and checksum;
- operator LAN map path and checksum;
- physical runtime identity joined to both paths;
- container-level LAN reachability result;
- container-level Tailscale reachability result;
- selected path shown by `/admin/admission`;
- LAN-to-Tailscale failover test;
- Tailscale-to-LAN preference restoration test;
- confirmation that both paths share one admission counter.

A remote Tailscale peer may be discovered while off-site, but it must not be automatically admitted or used to load a model.
