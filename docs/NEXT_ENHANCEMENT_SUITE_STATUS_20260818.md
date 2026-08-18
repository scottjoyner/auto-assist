# AssistX Next Enhancement Suite — Implementation Status — 2026-08-18

## Current state

All four software/CI lanes from `NEXT_ENHANCEMENT_SUITE_20260818.md` are implemented on their existing experiment branches. The remaining acceptance work is real fleet/runtime evidence, not missing architecture.

## Lane status

### Graph-assisted scope discovery — #32 / PR #30

Implementation head: `b1969e57276b464a0d810cfc4b2652d843e820c8`.

The bounded `ScopeAdvice` API now runs before execution-contract creation and defaults to **precision-only depth-1 advice** (`expansion_budget=0`). Repository-scale evidence showed:

| Budget | Mean recall | Mean precision |
|---|---:|---:|
| 8 files / 0 expansion | 24.62% | **60.10%** |
| 8 / 2 | 22.11% | 39.19% |
| 8 / 4 | 18.23% | 32.26% |
| 10 / 2 | 26.92% | 41.49% |
| 10 / 4 | 23.68% | 31.85% |

Graphify smoke, repository evidence, and implementer handoff are green. Remaining acceptance work is real coding-task shadow evidence for search/file-read reduction.

### Context-compression output equivalence — #33 / PR #29

Implementation head: `36c524b0a93799f995fa1d19bde976693277c63f`.

The endpoint runner is manifest-driven and repeatable with raw/lossless/Headroom/hybrid variants, default 10 repeats, explicit runtime/model/quant/context/source metadata, and durable `complete` versus `unavailable` states. Per-variant `output_equivalence_rate` is emitted against raw correctness.

Headroom package smoke, adversarial corpus evidence, and implementer handoff are green. Remaining acceptance work is one real OpenAI-compatible local-model endpoint run with correctness/TTFT/latency evidence.

### Held-out procedural memory — #34 / PR #31

Implementation head: `0c97f4f5e99ae4044356ada4392c759449adff1c`.

A broad retrieval configuration first failed the >=75% support gate at ~41.7%; the gate was preserved and retrieval narrowed to the intended selective top-2 surface. The retained deterministic held-out artifact reports:

- eligible support rate: **100%**;
- contradiction rate: **0%**;
- repeated-error reduction: **50%**;
- task success rate: **75%**;
- authoritative behavior changed: false.

Procedural held-out, Neo4j Agent Memory smoke, and implementer handoff are green. Remaining acceptance work is reproducing benefit on a real sanitized session/outcome corpus.

### Cache-affinity runtime telemetry — `auto-router` #14 / PR #13

Implementation head: `d124ed0a0914046533207e3018be7283c11f95a5`.

Typed request/candidate cache telemetry now carries model hash, quant, context size, runtime, session, stable-prefix identity, TTFT, prefill and cache-hit fields. Invalid/duplicate identities fail closed. The TTFT report compares authoritative and affinity-preferred eligible candidates.

Auto-router repository CI and the dedicated telemetry workflow are green. Artifact `9331664175` is explicitly marked `synthetic_live_shaped_fixture=true`, `promotion_evidence=false`, and `authoritative_behavior_changed=false`; it contains six comparable plumbing observations and zero routing-safety regressions.

Remaining acceptance work is >=10 real runtime observations before evaluating the >=10% median-TTFT gate.

## Auto-assist repository-wide CI note

The lane-specific workflows above are not the source of the current full-repository `auto-assist` CI red state. Fresh merge-ref CI exposes separate baseline integration debt, including Ruff formatting/import issues in unrelated modules/scripts and nine unit failures because current `fleet_node_agent` no longer exposes the historical `execute_task` API expected by benchmark/KV-cache/recovery/shell-gate tests. In that run, 384 tests pass and recovery/degraded-disaster canaries remain green.

This should be restored as a separate baseline-CI tranche rather than duplicated into each experiment branch.

## Completion boundary

```text
Graph scope advice          software complete -> real coding shadow evidence pending
Compression equivalence     software complete -> reachable model endpoint pending
Procedural held-out memory  software complete -> real session corpus pending
Cache-affinity telemetry    software complete -> real runtime telemetry pending
```

No experimental lane has production authority. Canary eligibility remains gated by repeated evidence, operational thresholds, fault gates, and explicit operator approval.