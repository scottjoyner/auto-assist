# Fleet Capability Routing Deployment

## Objective

Discover the complete Tailscale network, retain every peer in AssistX inventory,
and route work according to what each machine is actually permitted and proven to
do. A node does not need to run a complete Hermes or OpenCode instance to
contribute. Smaller or older machines can perform bounded summarization,
compression, extraction, or benchmark work while stronger nodes retain coding,
tool-use, reasoning, and long-context capacity.

This deployment joins three repositories:

```text
Tailscale status + operator role policy + exact-loadout benchmarks
                              |
                              v
                  lms fleet routing matrix
                  admission.admitted = false
                              |
                              v
                     AssistX / Neo4j
       complete node inventory + role policy + benchmark profiles
              + allocation + approval + claims + leases
                              |
                              v
              signed AssistX runtime projection
          admitted loaded runtimes/models and benchmark hints only
                              |
                              v
                       auto-router
       role eligibility -> quality floor -> utility -> live load/path
```

## Non-negotiable boundaries

- **Discovery is not admission.** `tailscale status --json` may reveal phones,
  tablets, routers, servers, and powered-off machines. Every peer is visible, but
  an unknown peer defaults to `observer_only` and cannot receive work.
- **Role policy is not runtime admission.** Assigning `summarization` or
  `full_agent` does not prove that a model is loaded or reachable.
- **Benchmarks are advisory evidence.** They cannot load models, grant tools,
  create access paths, or bypass claims.
- **Only AssistX admits capacity.** The signed runtime projection still requires
  approved physical runtime identity, loaded-model identity, capacity, private
  access paths, freshness, and operator approval.
- **auto-router remains a gateway.** It orders only the models already present in
  the signed AssistX projection.

## Worker modes

| Mode | Eligible work | Full Hermes/OpenCode required |
|---|---|---|
| `observer_only` | inventory and topology only | No |
| `benchmark_only` | controlled inventory/benchmark workflows | No |
| `auxiliary` | summarization, compression, extraction, optional bounded reasoning | No |
| `agent` | approved Hermes/OpenCode execution contracts and role-specific tasks | Yes, when the role requires it |

The default for any unlisted Tailscale peer is `observer_only`.

## 1. Update all three repositories

```bash
cd ~/git/lms && git pull --ff-only
cd ~/git/auto-assist && git pull --ff-only
cd ~/git/auto-router && git pull --ff-only
```

Install the current LMS tooling:

```bash
cd ~/git/lms
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
```

## 2. Create the operator-owned role policy

```bash
mkdir -p ~/.config/lms-fleet ~/.local/state/lms-fleet ~/lms-fleet-runs/compare
cp ~/git/lms/examples/fleet-role-policy.v1.json \
  ~/.config/lms-fleet/fleet-role-policy.v1.json
$EDITOR ~/.config/lms-fleet/fleet-role-policy.v1.json
```

Review every machine. Suggested starting roles:

- `x1-370`, `xwing`: full-agent, coding, tool-use, reasoning, and long-context
  work after exact-loadout qualification;
- `scotts-macbook-air`: full or auxiliary work according to current Metal
  benchmark evidence;
- `scotts-macbook-pro-2`, `deathstar-xps-8920`, `destroyer`,
  `beelink-ryzen-7-mini-pc`, `scott-lenovo-ideapad-330s-15ikb`, and
  `scott-optiplex-9030-aio`: auxiliary roles unless benchmark evidence justifies
  broader work;
- `joyner` while powered off: `benchmark_only` with deferred benchmarking;
- every other Tailscale peer: leave unlisted or explicitly set `observer_only`.

Do not copy a role merely because two devices have similar names. The policy is
operator authorization, not hardware inference.

## 3. Place benchmark evidence

The matrix accepts one or more JSON artifacts. The strongest input is the exact
loadout comparison produced by LMS:

```bash
lms-loadout-compare \
  --report ~/lms-fleet-runs/reports/x1-370-qwen.json \
  --report ~/lms-fleet-runs/reports/optiplex-summary.json \
  --out ~/lms-fleet-runs/compare/exact-loadouts.json
```

An exported auto-router value matrix can also be included:

```bash
curl -fsS \
  -H "X-Admin-Token: $AUTO_ROUTER_ADMIN_TOKEN" \
  http://127.0.0.1:8088/api/fleet/value-matrix \
  > ~/lms-fleet-runs/compare/router-value-matrix.json
```

Exact-loadout evidence remains tied to model artifact, quantization, runtime,
context, KV-cache, batching, and concurrency. Re-run benchmarks after any of
those dimensions change.

## 4. Configure secure publication

Create `~/.config/lms-fleet/full-fleet.env` with mode `0600`:

```bash
cat > ~/.config/lms-fleet/full-fleet.env <<'EOF'
BASIC_AUTH_USER=admin
BASIC_AUTH_PASS=replace-from-secret-store
ASSISTX_BASE_URL=http://127.0.0.1:8000
LMS_FLEET_MIN_DISCOVERED_NODES=3
LMS_FLEET_PUBLISH_TO_ASSISTX=true
EOF
chmod 600 ~/.config/lms-fleet/full-fleet.env
```

The discovery-count gate defaults to three. For the known ten-node benchmark
fleet, set a higher minimum after verifying which Tailscale peers should be
present. The gate is intended to detect accidental one- or two-node partial
views, not to require powered-off devices to report online.

## 5. Build and import the matrix

```bash
set -a
. ~/.config/lms-fleet/full-fleet.env
set +a

~/git/lms/scripts/refresh-fleet-routing-matrix.sh
```

The command fails when fewer than `LMS_FLEET_MIN_DISCOVERED_NODES` are visible or
when the artifact is not explicitly non-admitting.

Inspect the local artifact:

```bash
jq '{summary, nodes: [.nodes[] | {
  node_id,
  online,
  worker_mode,
  roles,
  tailscale_ips
}]}' ~/.local/state/lms-fleet/fleet-routing-matrix.json
```

Inspect the durable AssistX copy:

```bash
curl -fsS -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  http://127.0.0.1:8000/api/fleet/routing-matrix | jq
```

Required checks:

```bash
DISCOVERED="$(jq -r '.summary.tailnet_nodes' \
  ~/.local/state/lms-fleet/fleet-routing-matrix.json)"
test "$DISCOVERED" -gt 2

jq -e '.admission.admitted == false' \
  ~/.local/state/lms-fleet/fleet-routing-matrix.json

jq -e '[.nodes[] | select(.worker_mode == "observer_only")] | length >= 0' \
  ~/.local/state/lms-fleet/fleet-routing-matrix.json
```

## 6. Enable periodic tailnet refresh

Install the user-level units:

```bash
mkdir -p ~/.config/systemd/user
cp ~/git/lms/deploy/systemd/lms-fleet-routing-matrix.service \
  ~/.config/systemd/user/
cp ~/git/lms/deploy/systemd/lms-fleet-routing-matrix.timer \
  ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now lms-fleet-routing-matrix.timer
systemctl --user start lms-fleet-routing-matrix.service
systemctl --user status lms-fleet-routing-matrix.service
systemctl --user list-timers lms-fleet-routing-matrix.timer
```

The default cadence is every five minutes. The service executes a one-shot
refresh and does not remain privileged or resident.

## 7. Verify AssistX topology and policy

AssistX adds every imported Tailscale peer to its read-only router context:

```bash
curl -fsS -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  http://127.0.0.1:8000/api/router/context-projection \
  | jq '{count: (.nodes | length), nodes: [.nodes[] | {
      node_id, lane, running, capabilities, detail
    }]}'
```

Expected behavior:

- observer-only peers appear with `lane: blocked`;
- auxiliary workers appear in the local lane with only their approved roles;
- offline nodes remain visible with `running: false`;
- static AssistX/Neo4j/Redis service nodes remain present;
- the projected node count is greater than two when the tailnet census is
  healthy.

## 8. Approve runtime capacity separately

Do not route a node merely because it appears in the matrix. A runtime must still
have:

1. stable `node_id` and `runtime_instance_id`;
2. fresh physical process/runtime observation;
3. fresh loaded-model instance identity and artifact fingerprint;
4. exact quantization and context information;
5. approved LAN/Tailscale access paths;
6. approved slot/queue capacity;
7. completion canary evidence;
8. canonical fleet projection approval.

Only those records enter `/api/router/runtime-projection`. Observer-only and
benchmark-only devices never appear as providers.

## 9. Configure task-family requests

AssistX tasks should include a concrete family in their payload:

```json
{
  "queue_class": "background",
  "task_family": "compression",
  "prompt": "Compress this context while preserving decisions and identifiers."
}
```

Supported families are:

- `coding`
- `reasoning`
- `tool_use`
- `long_context`
- `summarization`
- `compression`
- `extraction`

Auto-router also recognizes these model aliases:

- `auto/summarize` and `auto/summary`
- `auto/compress` and `auto/compact`
- `auto/extract` and `auto/parse`

For OpenAI-compatible requests, prefer explicit metadata:

```json
{
  "model": "auto/compress",
  "messages": [{"role": "user", "content": "..."}],
  "metadata": {
    "task_family": "compression",
    "privacy": "local_only"
  }
}
```

## 10. Routing order

For AssistX assignment:

1. current task status and priority;
2. operator worker mode and role eligibility;
3. task-family quality floor;
4. task-family quality, throughput, reliability, and confidence;
5. current load and displacement cost;
6. learned task reliability;
7. KV-cache compatibility/locality and session affinity.

For auto-router selection among admitted models:

1. role eligibility;
2. measured quality-floor pass;
3. task-family benchmark utility and quality;
4. existing live admission, queue, path, health, and load balancing.

A fast low-quality model is ordered below an unmeasured model when its measured
quality floor fails. An auxiliary node cannot win coding merely because it has
high throughput.

## 11. End-to-end canary

Create three bounded tasks:

1. summarization expected to select an auxiliary or efficient full node;
2. compression expected to select an auxiliary node with the best qualified
   throughput/quality evidence;
3. coding expected to select only a `full_agent` or `code_agent` node with code
   execution allowed.

For each task verify:

```text
READY -> allocation recommendation -> reservation -> claim -> heartbeat
-> auto-router route -> exact runtime/model provenance -> completion
```

Negative canaries:

- observer-only Tailscale peer is visible but never placeable;
- auxiliary-only peer is rejected for coding;
- quality-floor failure cannot win on speed;
- offline peer remains visible but is not eligible;
- benchmark matrix cannot create a runtime provider;
- stale or expired runtime projection is rejected;
- stale claim is rejected immediately before dispatch.

## 12. Rollback

Stop scheduled imports:

```bash
systemctl --user disable --now lms-fleet-routing-matrix.timer
```

Restore the prior role policy or comparison artifacts and run one manual refresh.
Because the artifact does not admit capacity, removing it does not unload models
or revoke approved runtimes. To stop benchmark influence while preserving
inventory, import a matrix with nodes and no profiles. Runtime admission and task
claim fencing remain independently enforced.
