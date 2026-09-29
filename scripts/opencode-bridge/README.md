# opencode ↔ AssistX / auto-router Hybrid Bridge — STATUS

Last updated: 2026-07-17.

## Bridge architecture (3 hybrids, all wired)
- **A) Dynamic sync** — `sync_configs_to_assistx.py`
  Reads BOTH the opencode config (xwing local + x1-370 mirror) AND the Hermes
  `config.yaml` fleet block, derives ModelEndpoints per provider, labels each by
  its real tailnet node (IP->node map), dedups by IP:PORT, registers+probes into
  AssistX idempotently, then refreshes auto-router. Re-run (or cron) to keep
  AssistX in sync with the configs. This is the "configs dynamically go into
  AssistX" piece.
    - Run: `ASSISTX_PASS=fuck-you python3 sync_configs_to_assistx.py`
    - `--dry-run` to preview.
- **B) Router-aware** — `router_pick.py`
  POSTs to auto-router `/api/routes/request` with a task profile; returns the
  chosen provider/model. WORKS — but only honors `request.model` when that model
  is actually LOADED on its node (see "Critical finding" below).
- **C) Dispatch** — `dispatch_assistx_task.py`
  Emits a Task envelope (READY) to AssistX `/api/events`; Hermes adapter
  claims/executes with Neo4j traceability.

opencode command entries wired: `/router-pick`, `/fleet-register`, `/dispatch`.

## On-request refresh (every request syncs first)
Both `router_pick.py` (Hybrid B) and `dispatch_assistx_task.py` (Hybrid C) run the
Hybrid A sync (opencode + Hermes configs -> AssistX swarm + auto-router refresh)
BEFORE each request, by default. So every `/router-pick` and `/dispatch` pushes
the freshest fleet state into the swarm and refreshes auto-router, then acts.
No cron needed — the fleet is always current at request time. Pass `--no-refresh`
to skip the sync and act against last-known state (faster, for high-frequency
calls). Both opencode commands pass `ASSISTX_PASS` so the refresh has credentials.

dispatch_assistx_task.py envelope follows the live EventEnvelopeIn contract:
`subject.id` is required (set = task_id), and `privacy` needs `pii`,
`privacy_class` (public|private|sensitive|unknown), and `retention_class`
(keep|...). The canonical example used: {"pii": false, "privacy_class": "public",
"retention_class": "keep"}.

## Critical finding (corrected 2026-07-17) — NOT a router bug
The router "ignoring model requests / pinning joyner" is NOT a code defect.
auto-router only advertises models that are *resident* (`loaded_instances` !=
empty) — by design, to avoid routing to empty endpoints. The entire fleet
currently has **0 models loaded** (all registered but unloaded):
    x1-370        0/24   xwing     0/5    macbook_air 0/4
    deathstar     0/20   destroyer 0/11   optiplex   0/8
So `_exact_model_plan` matches nothing -> falls back to default lane (joyner).
FIX (operational, on each node): LOAD the models you want routable via LM Studio
on that node. Once loaded, the router instantly exposes + honors `request.model`.
No auto-router code change needed. (The earlier "1 model" reading was a transient
state mid-load during the live swarm test.)

## Delivered this session
1. Reconstructed the CORRUPT x1-370 opencode mirror
   (`/media/scott/SSD_4TB/hermes-home/home_scott_.config/opencode/opencode.jsonc`)
   from the authoritative live LM Studio model list (24 ids). It was broken JSON
   at line 451 with no intact backup. Now valid; the dynamic sync ingests it.
   Also fixed x1-370's stale gemma id (`google/gemma-4-31b` -> `gemma-4-31b-it`).
2. `sync_configs_to_assistx.py` merges BOTH configs (xwing + x1) and dedups by IP,
   yielding 7 endpoints incl. the lenovo node that only exists in x1's config.
3. Verified auto-router `/api/routes/request` is reachable and returns structured
   `{provider, model, lane, target_node_id, confidence, route_id, dispatch_id}`.

## Open items (operational, need you on the nodes)
- Load models on the nodes you want routable (esp. x1-370 apex) so Hybrid B fires.
- Duplicate swarm endpoints from before (deathstar-xps-8920, scott-optiplex-9030-
  aio) still exist; the DELETE endpoint 500s. New sync uses IP-prefixed IDs so it
  won't add more, but old dups should be deleted once reachable.
- Hermes fleet block is `enabled: false` in `~/.hermes/config.yaml` — enabling it
  (tailnet kipnerter.ts.net) would let Hermes natively discover the same nodes.

## File map
- scripts/opencode-bridge/sync_configs_to_assistx.py   (DYNAMIC sync — use this)
- scripts/opencode-bridge/router_pick.py               (Hybrid B client)
- scripts/opencode-bridge/dispatch_assistx_task.py     (Hybrid C)
- opencode config: ~/.config/opencode/opencode.local-fleet.json (symlinked active)
