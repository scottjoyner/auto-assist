# Deathstar local inference acceptance — 2026-10-10

**Decision:** direct-chat canary PASS; AssistX gateway/production and iOS 1.7 physical acceptance remain NO-GO.

## Scope and custody

Deathstar was approved for bounded worker evaluation only. Existing llama.cpp
endpoints were used without model loading, changing service configuration, or
interfering with Neo4j, NAS, dashcam rsync, or the installed iPhone build.
The probe permits only a small existing private/loopback endpoint with a
maximum of 32 output tokens. No private prompt or response is recorded.

Tools:
- scripts/local_inference_canary.py: bounded preflight health/props and direct
  OpenAI-compatible chat, optional strict SSE [DONE], backend token usage and
  first-content timing with metadata-only private receipts.
- scripts/summarize_local_inference_canaries.py: private same-owner 0600 file
  checks, 24h read-only rollup, duplicate suppression, usage consistency and
  cache-safe counting. No NAS or graph operations.

The synthetic prompt is a fixed harmless READY instruction. Only hashes are
stored for prompt, response, and requested model. Fifteen protected receipts
remain in ~/.local/state/assistx-local-canary/, not in the repository.
Sorted filename/file-SHA256 root: 
f0426b19d6fc9ce05388689c467d43d44f1010726e8273d16f0f3e8643770435
(This digest supports later integrity comparison, not device authenticity.)

## Measured results, October 10, 19:17–19:24 UTC

| Path | Requests | Quality | Usage | E2E latency |
|---|---:|---:|---|---|
| Raw /completion | 5 | 0/5 exact answer | Unknown | 651–876 ms |
| OpenAI-compatible chat | 5 | 5/5 exact answer | Complete | 473–538 ms |
| SSE chat | 5 | 5/5 exact answer and terminal | Complete | 480–514 ms |

From 10 qualifying chat requests: 140 input, 30 output, **170 total**
backend-reported tokens. Cached prompt tokens: 90, ALREADY within the 140
input tokens. Do not add cache twice. Five raw-completion runs have unknown
standardized usage and are not counted as zero. Backend-reported cost was
not independently verified against electricity or any external billing.

Combined structured-chat latency p50 506.97 ms, p95 532.19 ms.
Streaming first-content p50 465.27 ms, p95 483.99 ms.
This is a small, repeated, warm-cache and tiny-answer sample; not a sustained
throughput, large-model quality, user acceptance, or cross-node benchmark.

Rollup observed 15 requests, 10 quality passes, 5 SSE terminal passes,
10 complete standard usages, 5 unknown, 0 inconsistent and 0 rejects.
The report correctly shows **0 gateway-confirmed requests**.

## Existing operational constraints

Deathstar i7-7700: 4 cores / 8 threads; RAM ~13 GiB available.
Radeon GPU 8 GiB VRAM, about 4.85 GiB used. Neo4j and NAS dashcam
rsync were still present after the test; CPU load ~1.3, occasional low I/O
pressure. Storage recovery retains priority.

Central OpenCode harvester still connects as scott through the deathstar
SSH alias, but the authoritative 24-session database is owned by deathstar.
October 7 NAS archive preserves 24 historical sessions; latest current
OpenCode session dated August 26. The data-account roster fix is NOT applied.

Fleet reporter advertises 0 loaded models despite healthy direct endpoints.
Separately, AssistX router health alternated OK and degraded; runtime
projection renewal holds with foreign_revision. Unauthenticated mobile
catalog returned 401 as expected. None of these observations authorizes
a fresh runtime projection, production route or TestFlight promotion.

## Remaining deployment and human acceptance gates

1. Update data-owner roster only through its authorized management path;
   use deathstar@deathstar for the DB archive, preserve scott fleet SSH,
   and regression-test other Hermes roster consumers.
2. Correct model residency reporting and rate-limit/admission sources.
3. Capture authenticated AssistX gateway usage at real request/attempt
   boundaries, scoped by trusted tenant, node, producer and model. Dedup
   OpenCode/Hermes/llama.cpp observations and preserve unknown token cases.
4. Prove authorized gateway-to-Deathstar streaming with matching request
   IDs, W3C traceparent, backend usage receipt, cancel and offline failures.
5. Compare qualified same-model short and long tasks across other nodes,
   preserve NAS and Neo4j performance, admit only bounded worker leases.
6. Prove iOS rollback custody (#398) and distribution signing (#399),
   then run exact-source 1.7 physical iPhone acceptance (#400).
   Do not replace the installed 1.6.2 app solely from build-only success.

AssistX tracking: https://github.com/scottjoyner/auto-assist/issues/282
iOS RC0: https://github.com/scottjoyner/kipnerter-ios/pull/395
iOS release gates: https://github.com/scottjoyner/kipnerter-ios/issues/396

**Promotion remains NO-GO.** This patch provides a portable local inference
witness and real token evidence, not a deployed fleet token pipeline.
