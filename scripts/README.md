# AssistX Swarm Node Registration Guide

## Quick Registration (one-liner)

```bash
curl -s -u admin:change-me -X POST \
  -H "Content-Type: application/json" \
  -d '{"node_id":"falcon","hostname":"demo-1","display_name":"Falcon (demo-1/WSL2)","status":"online","roles":["hermes_agent","model_endpoint"],"tailscale_ip":"100.65.68.58","lan_ip":null,"os":"linux","arch":"x86_64"}' \
  http://172.20.0.5:8000/api/swarm/nodes/register
```

## Self-Registration Scripts

### Python (recommended)
```bash
python3 /home/scott/git/auto-assist/scripts/register_node.py \
  --node-id falcon \
  --display-name "Falcon (demo-1/WSL2)" \
  --tailscale-ip 100.65.68.58 \
  --os linux \
  --arch x86_64
```

### Bash
```bash
./register-node.sh falcon "Falcon (demo-1/WSL2)" 100.65.68.58 linux x86_64
```

## Environment Variables

Set these to avoid passing credentials each time:
```bash
export ASSISTX_URL=http://172.20.0.5:8000
export ASSISTX_USER=admin
export ASSISTX_PASS=change-me
```

## Current Swarm Nodes

| Node | Display Name | Tailscale IP | Status | Roles |
|------|-------------|--------------|--------|-------|
| falcon | Falcon (demo-1/WSL2) | 100.65.68.58 | online | hermes_agent, model_endpoint |
| x1-370 | x1-370 (primary - AssistX control plane) | 100.64.43.123 | online | - |
| scotts-macbook-air | kipnerter (MacBook Air) | 100.85.64.117 | online | hermes_agent, model_endpoint |
| deathstar-xps-8920 | deathstar XPS 8920 (Linux, legacy host) | 100.78.106.121 | online | hermes_agent, model_endpoint |
| destroyer | destroyer (Linux) | 100.81.57.77 | online | hermes_agent, model_endpoint |
| scott-optiplex-9030-aio | OptiPlex 9030 AIO (Linux) | 100.69.158.114 | online | hermes_agent, model_endpoint |
| demo-1 | demo-1 (Ubuntu WSL2, LM Studio) | 100.65.68.58 | online | hermes_agent, model_endpoint |
| demo | demo (Windows, LM Studio) | 100.67.106.114 | online | model_endpoint |
| raspberrypi | Raspberry Pi (Pi-hole) | 100.114.88.89 | idle | dns, network |

## Model Endpoints

Each agent with LM Studio has a model endpoint registered. Register one:
```bash
curl -s -u admin:change-me -X POST \
  -H "Content-Type: application/json" \
  -d '{"model_endpoint_id":"falcon.lmstudio","node_id":"falcon","base_url":"http://100.96.18.65:1234/v1","provider":"lm_studio","status":"online","auth_type":"none","network_preference":"tailscale","purpose":"llm.chat"}' \
  http://172.20.0.5:8000/api/swarm/model-endpoints/register
```

List all endpoints:
```bash
curl -s -u admin:change-me http://172.20.0.5:8000/api/swarm/model-endpoints
```

## Troubleshooting

- **Node not showing up**: Check AssistX URL and credentials
- **Wrong tailscale IP**: Re-register with correct IP
- **Node stuck offline**: Check network connectivity to AssistX
