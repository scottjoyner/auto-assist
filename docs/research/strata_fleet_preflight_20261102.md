# Strata v0.2.10: actual fleet hardware and non-disruptive preflight

**Inspection date:** 2026-11-02 23:52 UTC–2026-11-03 00:20 UTC
(US Eastern: November 2, 2026). **Research-only, production HOLD.**

## Why the starred repository is worth testing

[Niko1221/Strata](https://github.com/Niko1221/Strata) is a MIT-licensed
local inference engine with an OpenAI/Anthropic-compatible server. Its
[AMD HIP guide](https://github.com/Niko1221/Strata/blob/main/docs/AMD_HIP.md)
explicitly lists Radeon AI PRO R9700 `gfx1201` as maintainer-tested and
[Strix Halo `gfx1151`](https://github.com/Niko1221/Strata/blob/main/docs/STRIX_HALO.md)
as experimental. These match two existing inference fleet candidates.

Crucially, the upstream guide explicitly says **Strix Point/890M
`gfx1150` is not supported by its standard setup**, even though
it is an AMD GPU. Do not treat all AMD GPUs as interchangeable.

Latest release verified through GitHub's release API:
[Strata v0.2.10](https://github.com/Niko1221/Strata/releases/tag/v0.2.10),
published **November 2, 2026 at 23:52:47 UTC**.
GitHub release assets include substantial, architecture-specific
engine archives (for example `strata-engine-0.1.56-linux-x86_64-hip-halo.tar.gz`
~219 MB, before model weights) and a signed release manifest.
**No archive, model, installer or driver was downloaded or executed
in this experiment.** No release-asset hash or signature was locally
validated; the release metadata was inspected only.

Strata's Quick Start provides `strata launch --plan`, but this
can still depend on installed control tools. The project intentionally
does **not** attempt to run even that command on production hosts.

## Real node evidence, captured read-only

Read-only inspection via Fleet Commander on **x1-370**, and
authenticated, host-key-checked SSH read-only probes to `joyner`
and `xwing`, followed by running the exact research script over
SSH stdin on those two remote hosts. No persistent files were
written by the preflight. No pre-existing model, inference process,
systemd unit, kernel driver, scheduler, graph DB or agent route
was stopped, restarted, modified or replaced.

| Node | PCI ID / target | Hardware verdict | Available RAM at script run | Free root at script run | Running inference process | Preflight |
| --- | --- | --- | ---: | ---: | --- | --- |
| `x1-370` | AMD `1002:150e`, `gfx1150` | **Not supported by Strata setup** | ~61.4 GiB | ~29.2 GiB | not established by named-process scan | HOLD |
| `xwing` | AMD `1002:1586`, `gfx1151` | **Experimental** `halo` variant | **6,167 MiB** | **29.627 GiB** | `llama-server` | **HOLD** |
| `joyner` | AMD `1002:7551`, `gfx1201` | **Supported** `rx9070` variant | **1,208 MiB** | **285.737 GiB** | `llama-server` | **HOLD** |

The direct `x1-370` check succeeded locally. Its own SSH
alias did not authorize key-based access from that machine; that
self-SSH failure is immaterial to the local hardware finding.

`joyner` also has AMD Raphael integrated graphics `1002:164e`,
which the narrow mapping deliberately leaves unknown; the selected
candidate is the discrete R9700. This is not evidence that all other
host GPUs lack support. The script uses *only* explicit verified
PCI IDs.

The conservative **research trial reserve floors** are at least
8,192 MiB of MemAvailable and 40 GiB of available root storage.
These are **preflight heuristics, not actual model-package RAM or
disk requirements**. A model may need far more; an existing model
or secondary mounted store may change the actual disk budget.
Nothing clears the production admission gate even if all these
floor checks pass.

The current `llama-server` processes appear to provide valuable
inference capacity. Terminating them to make room for a second
engine is explicitly **not authorized**.

### Important limitations

- Process identity uses the Linux `/proc/<pid>/comm` executable
  name and an explicit allowlist. That is a bounded,
  **best-effort** check, not a full cgroup, port, accelerator VRAM,
  model state, or runtime-client inventory; it cannot prove that
  inference is absent or idle.
- `MemAvailable` is a host snapshot, not guaranteed capacity for
  an additional model. For xwing, CPU and GPU RAM are shared.
- `statvfs("/")` measures the root filesystem, not additional
  disk volumes, model caches or capacity reservations.
- GPU PCI IDs are cross-checked against AMD / upstream docs;
  no device initialization, ROCm ABI smoke, GPU kernel, HIP runtime,
  memory allocation or model generation was performed.
- The full latest release's engine hashes, signed manifests and
  runtime security were not independently verified in this sweep.
- Nothing was measured for actual tokens/s, latency,
  prefill/decode speed, model fit, energy consumption, concurrency
  or throughput relative to existing llama-server or LM Studio.

## Committed reusable read-only preflight

`scripts/experiments/strata_fleet_preflight.py` is Python
stdlib-only. It reads bounded `/sys/class/drm` GPU PCI IDs,
`/proc/meminfo`, `/proc/<pid>/comm`, and root free storage.
It has **no networking, subprocess, installs, model downloads,
driver changes, agent hooks, service starts/stops, or deployment
actions**.

It re-derives compatibility from known hardware PCI IDs rather
than trusting a user-supplied `gfx` or `supported` label.
It fails closed if process enumeration is incomplete, a required
resource is missing, no GPU match is verified, a reserve is
violated, or a running inference process is found. Its output
contains process *names* only, not arguments, environment
variables, prompts, keys, raw traces or port credentials.

Suggested read-only use on a Linux node:

```bash
python3 scripts/experiments/strata_fleet_preflight.py
```

Return codes:
`0` = **RESEARCH_PRECHECK_CLEAR** only (never installation or
production admission);
`3` = resource/compatibility/process **HOLD**;
`2` = malformed/incomplete evidence.

All receipts **always** set
`production_admission=HOLD`,
`model_or_engine_download_allowed=false`,
`service_install_or_restart_allowed=false`,
`inference_dispatch_allowed=false`,
`actual_inference_benchmark_executed=false`.

The accompanying
`tests/test_strata_fleet_preflight.py` uses canonical synthetic
host snapshots for all three named GPU targets, forged GPU label,
unknown devices, insufficient resources, live `llama-server`,
missing process scan, memory parsing, output safety and explicit
no-execution controls.

**25/25 focused tests PASS** on x1-370 for initial script.
The final source included a small later refinement to skip DRM
connector entries; the exact final head should be re-tested
separately before reporting hosted CI success.

## Smallest safe next stages

1. Preserve existing inference. Audit `joyner` and `xwing`
   live RAM/VRAM, GPU process residency and utilization,
   active request volume, and actual free persistent model
   storage **read-only**, without stopping llama-server.
2. Resolve a non-disruptive performance-test window or a
   spare node; document model *family* and target first.
   Strata's marketed families (Qwen3/DeepSeek) do not
   automatically accept the fleet's preferred LFM3 checkpoints.
3. Acquire a pinned platform engine only after checking
   its complete signed manifest and SHA-256 in a disposable
   isolated environment with sufficient disk/RAM, then
   test `strata --version` and an offline config plan.
4. Bench **the same already-available, properly licensed model**
   on both engines with equal prompt/context, request concurrency,
   prefill/decode counts, RSS/VRAM, power and signed trace receipts;
   do not compare different model families as an engine result.
5. Require explicit separate authorization for any running
   service, disk-changing install, scheduler registration or
   production cutover. Keep SGNC/trace provenance
   and fleet Auto-Router safety gates independent.

**Decision: research candidate, fleet rollout HOLD.**
