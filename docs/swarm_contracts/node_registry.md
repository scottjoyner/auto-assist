# Swarm Node Registry Contract

_Last updated: 2026-05-26_

## Purpose

The swarm node registry lets AssistX know which machines, services, model endpoints, workers, mounts, and agent runtimes are available.

AssistX should delegate based on registered capabilities, health, benchmarks, risk, and data locality.

---

## Node schema

```yaml
node_id: string
hostname: string
display_name: string
status: online | degraded | offline | maintenance
roles: list[string]
tailscale_ip: optional string
tailscale_name: optional string
lan_ip: optional string
os: linux | windows | macos | wsl | unknown
arch: x86_64 | arm64 | unknown
cpu_model: optional string
cpu_threads: optional int
memory_gb: optional float
gpu: optional string
gpu_memory_gb: optional float
power_profile: low | medium | high | burst
storage_profile: local_ssd | nas_adjacent | external | unknown
last_seen_at: ISO-8601
created_at: ISO-8601
updated_at: ISO-8601
```

---

## Initial known nodes

### `x1-370`

```yaml
node_id: x1-370
roles:
  - primary_knowledge_host
  - control_plane_candidate
  - model_endpoint
  - high_memory_worker
power_profile: high
notes: Most powerful/high-memory node and likely first production control-plane candidate.
```

### `deathstar-XPS-8920`

```yaml
node_id: deathstar-XPS-8920
roles:
  - legacy_auto_ingest
  - hermes_agent
  - model_endpoint
power_profile: medium
notes: Former primary machine; continues selected legacy ingest jobs and can run Qwen-class local models.
```

### `mini-pc-22`

```yaml
node_id: mini-pc-22
roles:
  - future_always_on_service_host
  - utility_node
power_profile: medium
notes: Early-stage; may migrate to Ubuntu-only.
```

### `demo`

```yaml
node_id: demo
roles:
  - fast_delegation_agent
  - gpu_burst_worker
  - model_endpoint
power_profile: burst
notes: Powerful GPU but memory constrained; good for fast token generation.
```

### `demo-1`

```yaml
node_id: demo-1
roles:
  - fast_delegation_agent
  - gpu_burst_worker
  - model_endpoint
power_profile: burst
notes: Powerful GPU but memory constrained; good for fast token generation.
```

---

## Service endpoint schema

```yaml
endpoint_id: string
node_id: string
service_type: assistx_api | sophia_voice | neo4j | lmstudio | openai_compatible | hermes_agent | opencode | auto_ingest_worker | birdcam | redis | falkordb | other
base_url: string
network_preference: tailscale | lan | localhost
health_url: optional string
auth_type: none | basic | bearer | hmac | tailscale
status: online | degraded | offline | unknown
last_probe_at: ISO-8601
```

---

## Capability schema

```yaml
capability_id: string
node_id: string
kind: llm | stt | tts | embedding | vision | ingest | graph | file | planning | qa | code_edit | draft
name: string
inputs: list[string]
outputs: list[string]
requires_gpu: boolean
min_memory_gb: optional float
risk_allowed: low | medium | high
status: available | unavailable | degraded
```

---

## Neo4j model

```cypher
(:SwarmNode {node_id, hostname, status, tailscale_ip, lan_ip, roles, last_seen_at})
(:ServiceEndpoint {endpoint_id, service_type, base_url, status, last_probe_at})
(:Capability {capability_id, kind, name, status})
(:StorageRoot {storage_root, node_id, host_path, container_path})
(:HealthCheck {check_id, status, checked_at, details})
```

Relationships:

```cypher
(:SwarmNode)-[:EXPOSES]->(:ServiceEndpoint)
(:SwarmNode)-[:CAN_RUN]->(:Capability)
(:SwarmNode)-[:MOUNTS]->(:StorageRoot)
(:SwarmNode)-[:REPORTED]->(:HealthCheck)
```

---

## Registration endpoint

```http
POST /api/swarm/nodes/register
```

```json
{
  "node_id": "demo-1",
  "hostname": "demo-1",
  "roles": ["fast_delegation_agent", "model_endpoint"],
  "tailscale_name": "demo-1",
  "os": "linux",
  "capabilities": [
    {
      "capability_id": "demo-1.llm.chat.qwen9b",
      "kind": "llm",
      "name": "Qwen 9B chat",
      "inputs": ["text/plain"],
      "outputs": ["text/plain", "application/json"],
      "requires_gpu": true,
      "risk_allowed": "medium"
    }
  ]
}
```

---

## Health endpoint

```http
POST /api/swarm/nodes/{node_id}/heartbeat
```

```json
{
  "status": "online",
  "current_task_id": "optional",
  "load": {
    "cpu_percent": 35,
    "memory_percent": 62,
    "gpu_percent": 80
  },
  "services": []
}
```

---

## Implementation checklist

- [ ] Add `SwarmNode` schema and constraints.
- [ ] Add registration endpoint.
- [ ] Add heartbeat endpoint.
- [ ] Add service endpoint probe runner.
- [ ] Add Tailscale-first URL resolver.
- [ ] Add dashboard panel for nodes/services/capabilities.
- [ ] Add task router that filters by capability and health.
