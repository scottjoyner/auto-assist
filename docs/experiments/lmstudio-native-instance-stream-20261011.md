# Native LM Studio model-instance SSE witness — research only, 2026-10-11

**Draft-only. No inference sent, model load, credentials, node changes, private logs, signing keys, routing or approval issued.** Stacked on [AssistX PR #314](https://github.com/scottjoyner/auto-assist/pull/314) (paired signed gateway and *actual* worker receipt contract) and [#311](https://github.com/scottjoyner/auto-assist/pull/311) (unsigned GGUF worker + LM Studio gateway process/socket lineage). Related blocker: [AssistX issue #313](https://github.com/scottjoyner/auto-assist/issues/313).

## Verified public LM Studio interface (October 11, 2026)

LM Studio documents `POST /api/v1/chat` with `stream=true`; **`chat.start.model_instance_id`** identifies the loaded *model instance* selected for generation, while the terminating **`chat.end.result.model_instance_id`** and optional **`chat.end.result.response_id`** identify the completed chat response. The SSE stream explicitly has `model_load.start/progress/end` events if JIT loading occurs. The native chat API's `store=true` (default) supplies a stored `response_id`. Sources: [official SSE contract](https://lmstudio.ai/docs/developer/rest/streaming-events), [native chat API](https://lmstudio.ai/docs/developer/rest/chat), [server/JIT settings](https://lmstudio.ai/docs/developer/core/server/settings).

These fields **do NOT expose a Linux worker PID, worker boot UUID, process start ticks, GGUF artifact checksum, current source signer, or authenticated two-user execution custody**. The stored chat response ID is not the caller's authenticated request ID. An instance ID is still not a physical process receipt.

Physical read-only Beelink evidence from PR #311: gateway `lm-studio` PID 1535 owns API port 1234, separately hashed GGUF `llama-server` PID 4783 is its child and owns direct port 45435. `GET /api/v1/models` disclosed a loaded instance with `id` and `config` fields **but no worker PID**, while direct worker `GET /v1/models` returned HTTP 401. This new experiment does not use credentials, bypass worker protection, or send an inference to identify process affinity.

## New offline parser and explicit limits

`scripts/verify_lmstudio_native_stream_instance.py` accepts **caller-provided synthetic or previously authorized and sanitized SSE byte streams only**. No file I/O, server call, active listener, signing, model load or external endpoint. It enforces:
- One `chat.start`, exactly one ordinary message with bounded `message.delta` fragments and `message.end`, then exactly one `chat.end`. Missing, extra, reordered, duplicate or truncated events fail.
- The exact `chat.start.model_instance_id` equals a **previously selected loaded instance ID** supplied to the parser; `chat.end.result.model_instance_id` must match too.
- Explicitly reject ANY `model_load.*` event, `error`, reasoning content, tool call, unknown future event, or unexpected payload shape. Native model-load detection is a guard, not proof auto-loading could never happen under another endpoint.
- Verify final ordinary message equals assembled streamed deltas; require `resp_*` stored response ID and positive bounded `total_output_tokens`. SSE input ≤128 KiB, output text ≤8 KiB, ≤256 events, ≤128 admitted tokens. No raw prompt/answer or reasoning is emitted.
- Compute SHA256 of a **single simple message's UTF-8 content** and of the full SSE stream; output has `status=GATEWAY_MODEL_INSTANCE_ONLY`, `physical_worker_pid_attested=false`, `worker_execution_attested=false`, `owner_bound_request_attested=false`, `can_sign_physical_source=false`, `can_approve=false`, `can_dispatch=false`. Even a gateway-approved stream does not prove a separately signed physical worker execution.

**Privacy:** a SHA256 of a very short known canary response can reveal that response through guessing/dictionary attacks; do not hash or archive ordinary private chat content via this research tool. Only use fabricated text or a specifically authorized benign bounded canary. No source traces are ever published from this experiment. Signing arbitrary stream hashes with untrusted local keys would still not establish provenance.

## Test evidence

`tests/test_verify_lmstudio_native_stream_instance.py` includes **29 source-only synthetic SSE acceptance and negative cases**: valid preloaded model ID, CRLF, missing terminal boundary, stream too large/invalid UTF-8, unexpected load events, errors, tool/reasoning privacy guard, start/end wrong instance, invalid/missing response ID, token limits, different final output, duplicate/extra events, unexpected content fields, and non-authority flags. Initial exact-head scratch run on x1-370: **29 passed, strict Ruff passed**, no LM Studio server/model access.

In the stacked draft tree, CI also reruns [PR #314](https://github.com/scottjoyner/auto-assist/pull/314)'s 34 signed gateway/worker synthetic receipt tests, 17 unsigned physical cross-observation tests, and 15 socket-inode lineage tests: **95 focused tests**, all offline. These tests establish **protocol validation only** and will never be called live physical worker acceptance.

## Actual physical acceptance remains blocked

A useful future operator-authorized benign canary could collect one LM Studio native SSE stream and show:
1. Before requests, an approved model instance is **already LOADED** and resource admission, per-user identity and permission are in force; JIT loading disabled by operator-approved config.
2. Stream `chat.start` and `chat.end` select the same loaded instance, no model_load/errors, bounds are enforced and response is complete.
3. Independently trusted worker-side source records the **same** request ID/nonce and actual physical boot/PID/start ticks/GGUF digest, and signs output after complete inference. Public LM Studio's documented native stream does not currently provide this step, nor does `loaded_instances.id`.
4. Authenticated gateway independently binds user/request ownership and signs the exact worker receipt, using keys established by independent trusted custody and a durable request replay ledger.
5. Only then evaluate source-to-owner binding, operator approval, live schema migration and strict-offline behavior. **Do not auto-load a model or send unauthorized inference to obtain missing PID telemetry**.

Other pending release gates: 3 Neo4j uniqueness constraints, separate physical/gateway/operator key custody and revocation, archived signed source evidence, expired canonical 643 renewal by explicit authorized operator only, legacy unsigned retirement, [Auto-Router owner receipts #37](https://github.com/scottjoyner/auto-router/issues/37), and full AssistX CI repair. Remain draft.
