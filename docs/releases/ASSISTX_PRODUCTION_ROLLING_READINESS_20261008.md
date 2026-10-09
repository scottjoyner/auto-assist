# AssistX production rolling-release readiness — 2026-10-08 (EDT)

**Status:** PREPARATION / NO-GO; **not a deployment authorization**. **Maintenance window:** proposed 2026-10-15 21:00 to 2026-10-16 02:00 America/New_York; not booked/approved. **Raspberry Pi:** excluded from maintenance, benchmarks and rollout. Do not stop, restart or reconfigure it.

## 1. Provenance and current state

- Controller observed 2026-10-08 22:41 EDT via read-only Fleet Commander: `x1-370`; containers `assistx-api`, `assistx-worker`, `assistx-redis` and `neo4j` reported healthy; `assistx-hermes-adapter` running. This is **not** new-release acceptance.
- At that instant `x1-370` had approximately 60 GiB available system RAM, 7.9/8.0 GiB swap occupied, and `/nas` CIFS reported ~42 TB free. A mounted CIFS share **does not prove** NAS5 source identity, recovery status or write safety.
- Main integration basis: draft [#136](https://github.com/scottjoyner/auto-assist/pull/136) at `81bd993da2fb491522285ff77689a70657f79303`, based on main `a300072d11787a58a4587fa325f1b8fd66803730`. Offline Safety RC2 [#155](https://github.com/scottjoyner/auto-assist/pull/155) at `0e7e09f80c9686bf968d6c1c60f24b1b39843c3e`. Newest additive draft CI repairs [#161](https://github.com/scottjoyner/auto-assist/pull/161) at `34c4aa6e41c7e33188c832f3b7e29f1d356d90f5`.
- GitHub Actions `ci` [run 37872351776](https://github.com/scottjoyner/auto-assist/actions/runs/37872351776) on #161: unit **13 failed / 837 passed / 59 deselected / 9 warnings**, while recovery-canary job **passed**. CI itself **failed**. Upstream [#155](https://github.com/scottjoyner/auto-assist/pull/155) offline-safety focused workflow passed, but its full `ci` failed. No generic "green" claim.
- No production release commit, image digest, independently witnessed custody head, rollback artifact, or node maintenance approval has been nominated by this document. No physical negative-admission or trusted-ingress acceptance is claimed.

## 2. Candidate slicing and dependency discipline

1. **Production baseline:** retain the presently healthy, already deployed controller as rollback anchor. Do not infer its Git SHA or image digest from source PRs; capture them from the actual runtime by read-only attestation.
2. **Candidate A — observability-only:** read-only /traces, provenance-limited evidence, opt-in burn projection and synthetic Mercury stand-in from [#136](https://github.com/scottjoyner/auto-assist/pull/136) and [#138](https://github.com/scottjoyner/auto-assist/pull/138); no new execution authority. Must integrate only through a reviewed exact-head release branch and pass production-grade auth/performance gates.
3. **Candidate B — offline safety fixes:** strict source binding, UTF-8 output integrity, fail-closed Jev observer and auth test alignment from [#155](https://github.com/scottjoyner/auto-assist/pull/155) + [#161](https://github.com/scottjoyner/auto-assist/pull/161). Preserve strict reject semantics, no authorization relaxations. CI red is blocking.
4. **Deferred:** trace-issued real execution [#119](https://github.com/scottjoyner/auto-assist/pull/119), live provider quota/admission, and trace index concurrency prototype [#154](https://github.com/scottjoyner/auto-assist/pull/154)/[#160](https://github.com/scottjoyner/auto-assist/pull/160) are separate research gates. Do not silently ship them under this release.
5. **Optional later feature:** Kipnerter graph canary [#139](https://github.com/scottjoyner/auto-assist/issues/139) is independent and may run only if AssistX recovery and separate graph/source/physical-device acceptance finish; it does not consume automatic maintenance authority.

## 3. Exact-head CI remaining failures on #161 (all blocking)

| Classification | Test group | Failure count | Required disposition |
| --- | --- | ---: | --- |
| Dashboard contract | `tests/test_enhanced_dashboard.py` | 9 | Reconcile actual control-room payload/HTML with supported dashboard requirements; either fix production behavior and positive/negative tests, or explicitly remove unshipped feature from this release **with reviewer approval**. Never make tests green by fabricating health. |
| Secret custody | `tests/test_env_file_ignore_rules.py` | 1 | [P0 #156](https://github.com/scottjoyner/auto-assist/issues/156): tracked environment snapshot and unsafe example classification. Private credential custodians assess/rotate live values; scrub current tree safely without emitting deleted secrets in a PR diff; evaluate history exposure. No content/value publication. |
| Runtime routing | `tests/test_fleet_routing_matrix_import.py` | 1 | Restore exact projection API contract with deny-by-default semantics and evidence binding; do not route unknown capacity. |
| Kipnerter gateway | `tests/test_kipnerter_gateway_wiring.py` | 2 | Reconcile missing `.env.kipnerter-gateway.example` and `scripts/deploy-kipnerter-gateway.sh` with explicit feature scope; provide placeholder-only negative tests or formally defer integration. Never invent a deploy helper that bypasses source/Caddy fencing. |

**Retest:** compile + lint, 13 targeted regressions, complete `ci`, recovery canary, focused offline matrix, authenticated browser 375/768/1440px, and all explicitly applicable negative tests on **one exact integration commit**. No blanket xfails/skips, weakening assertions, or copying actual credentials into fixtures.

## 4. Non-negotiable production NO-GO gates

- [ ] **P0 credential exposure/custody [#156](https://github.com/scottjoyner/auto-assist/issues/156):** human custodian confirms exposure assessment and necessary rotations; Git tree & CI negative scan green. Avoid writing any secret values or revealing sensitive delete patches.
- [ ] **P0 ingress and trusted-header provenance [#149](https://github.com/scottjoyner/auto-assist/issues/149):** prove each ingress strips spoofed identity headers and injects trusted identity only after upstream authentication. Anonymous, forged, expired and wrong-role probes fail before Neo4j in *isolated staging*, not live API. Do not disable header mode silently.
- [ ] **Trace-query boundedness [#142](https://github.com/scottjoyner/auto-assist/issues/142), [#148](https://github.com/scottjoyner/auto-assist/issues/148):** explicitly decide whether expensive global trace index remains disabled for this release or obtain measured server-side per-request limits, safe peer identity and cancellation. Redis TTL slots are **soft**, not physical concurrency limits; [#160](https://github.com/scottjoyner/auto-assist/pull/160) reproduced overrun.
- [ ] **Trace/audit custody [#127](https://github.com/scottjoyner/auto-assist/issues/127) and [#117](https://github.com/scottjoyner/auto-assist/issues/117):** verify independent restoration, nontrim archival, chain integrity and rollback with secrets withheld from logs.
- [ ] **No production authority escalation:** physical negative admission, signed claims, revocation and replay denial remain missing for [#119](https://github.com/scottjoyner/auto-assist/pull/119); no live provider or command authority without independent approval.
- [ ] **Source/release identity:** one exact Git commit, clean tree, verified CI jobs, signed/tagged release artifact and immutable image digests for each service; source-to-image attestation and test provenance.
- [ ] **Operational preflight:** validated backup snapshots and restorability, migrations safe/reversible, exact Redis/Neo4j version and health, SSH/service owners, authenticated off-node audit; p95 error/latency baseline before any change.
- [ ] **Human operator GO:** explicit authorization with maintenance start/end, node outage policy, rollback operator, and selected SHA/digest. An .ics calendar import is not approval or an execution trigger.

## 5. Tentative rolling maintenance (not executable schedule)

Local time America/New_York, proposed 2026-10-15 21:00 – 2026-10-16 02:00:
- 21:00–21:30: preflight, custody proof, node inventory, no-go review, restore and rollback rehearsal.
- 21:30–21:55: Lenovo; 21:55–22:20: Beelink; 22:20–22:45: Destroyer; 22:45–23:10: Joyner.
- 23:10–23:35: Optiplex; 23:35–00:00: Deathstar; 00:00–00:25: Xwing; 00:25–00:50: X1-370 last.
- 00:50–01:30: conditional AssistX release rollout/health/rollback acceptance; 01:30–02:00: restore every service, audit and finalize.
- **One node at a time; Raspberry Pi excluded** from all outages/benchmarks. A node that fails restore blocks progression. Do not combine a storage owner and a dependent application outage accidentally.
- **Critical caveat:** `x1-370` hosts orchestration, Neo4j and API. Before considering it for benchmark/downtime, prove an independent off-node coordinator, control-plane continuity, trace/audit custody and return path. Otherwise **skip X1 downtime**; do not strand rollback with the coordinator offline.
- **Beelink NAS / NAS5:** never infer from `/nas` free capacity that a direct NAS5 source mount is safe. Treat source identity, write pressure, frozen manifest, recovery ledger and owner validation as separate release checks. Pause **only** eligible inference after explicit recovery approval; never kill NAS recovery/Nextcloud/storage guardians as incidental benchmark cleanup.
- Fine-tuning and GPU reservations: checkpoint owners explicitly or skip their node. No cache purge, model unload, OS reboot or driver modification without scoped approval.

## 6. Benchmark contract for each eligible node

Before maintenance record: hostname + immutable identity, CPU/GPU/driver/runtime, total/available RAM & VRAM, swap pressure, running model IDs/quant/commit, service bindings, storage dependencies, protected processes, rollback owner.
During approved node window: queue/drain new assignments; emit trace `maintenance_id`, `node_id`, `model_digest`, `runtime_digest`, `case_id`, `prompt_tokens`, `output_tokens`, `ttft_ms`, `prefill_tps`, `decode_tps`, `p50/p95`, `memory_peak`, `temperature`, `failure_reason`, timestamps and prediction-versus-observation deltas. Repeat comparable local/synthetic prompts with recorded seeds and explicit cold/warm states, then restore and verify health. No hosted-provider calls.
Hard-stop: audit gap, misplaced secrets, lost checkpoint, wrong-node admission, unhealthy API/Neo4j, unmounted/wrong NAS owner, unsanctioned process disturbance, thermal/disk pressure or failed service restoration. Restore the previous exact artifact, route, config and credentials by tested rollback; no speculative automated restart loops.

## 7. Exit criteria and ownership

**Release candidate accepted for staging only** when all exact-head tests pass, risk owners sign P0 dispositions, staging auth/ingress denial is evidenced, and every included feature explicitly states its production authority. **Production eligible** only after signed off-node rollback rehearsal, source-to-image proof, operator-owned GO and no unresolved P0.
At the end publish immutable release SHAs/digests, CI workflow URLs, secret-free redacted evidence manifest, per-node measured observations, alert baseline, rollbacks tested, open defects and a timestamped knowledge handoff. A failed gate yields **NO-GO**, not a partial silent cutover.

Related: [#139](https://github.com/scottjoyner/auto-assist/issues/139) calendar/rolling coordination, [#145](https://github.com/scottjoyner/auto-assist/issues/145) CI gates, [#137](https://github.com/scottjoyner/auto-assist/issues/137) observability acceptance, [#36](https://github.com/scottjoyner/auto-assist/issues/36) live deployment reconciliation.

## 8. Read-only SSH maintenance preflight — October 8, 2026, ~22:45 EDT

All figures are instantaneous snapshots from unattended SSH checks initiated by x1-370; no service mutations or benchmarks were performed. Used swap does not by itself prove ongoing swapping.

| Node | SSH | Mem available | Swap used | Root used | Release action |
| --- | --- | ---: | ---: | ---: | --- |
| Lenovo | pass | 8,229 MiB | 262 MiB | 63% | Candidate rolling benchmark after service inventory |
| Beelink Ryzen 7 | pass | 9,390 MiB | 2,877 MiB | 54% | **Storage/NAS recovery owner approval required** before inference drain |
| Destroyer | pass | 5,067 MiB | 8,185 MiB | 84% | **Pressure preflight**: assess model memory, write headroom, checkpoints and active swap-in/out before benchmark |
| Joyner | pass | 10,491 MiB | 2,292 MiB | 41% | Candidate, verify authorized service owner |
| Optiplex | Tailscale additional authentication required | not recorded | not recorded | not recorded | **Blocked** until unattended access is attested; do not auto-resolve SSH identity or proceed |
| Deathstar | Tailscale additional authentication required | not recorded | not recorded | not recorded | **Blocked** until unattended access and storage state attestations |
| Xwing | pass | 23,211 MiB | 10,517 MiB | 63% | **Pressure preflight** and protected experiment/checkpoint ownership; avoid model unload without approval |
| MacBook Air | permission denied (SSH publickey/password/keyboard-interactive) | not recorded | not recorded | not recorded | Not on rolling benchmark order; **not** an independently proven standby coordinator |
| X1-370 | local read-only | ~60 GiB | ~7.9/8.0 GiB | 70% | Last only if independent coordinator and rollback custody demonstrated |
| Raspberry Pi | intentionally excluded | not probed | not probed | not probed | **No outage / no benchmark / no configuration change** |

**Reconciliation consequence:** current live access does not support an unconditional eight-node benchmark. Do not let SSH/browser/Tailscale reauthentication dialogs silently become operational approval. Recover access with owner-controlled credential paths, capture evidentiary source and identity, and reschedule any node that cannot pass preflight. Disk/swap pressure, particularly on Destroyer, must be measured against actual protected service and active I/O before any shutdown or stress test.
