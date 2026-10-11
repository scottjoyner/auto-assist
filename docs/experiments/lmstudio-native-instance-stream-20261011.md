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

`tests/test_verify_lmstudio_native_stream_instance.py` includes **36 source-only synthetic SSE acceptance and negative cases**: valid preloaded model ID, CRLF, missing terminal boundary, stream too large/invalid UTF-8, unexpected load events, errors, tool/reasoning privacy guard, start/end wrong instance, invalid/missing response ID, token limits, different final output, duplicate/extra events, unexpected content fields, and non-authority flags. Initial exact-head scratch run on x1-370: **29 passed, strict Ruff passed**, no LM Studio server/model access.

In the stacked draft tree, CI also reruns [PR #314](https://github.com/scottjoyner/auto-assist/pull/314)'s 34 signed gateway/worker synthetic receipt tests, 17 unsigned physical cross-observation tests, and 15 socket-inode lineage tests: **118 focused tests**, all offline. These tests establish **protocol validation only** and will never be called live physical worker acceptance.

## Official `prompt_processing.*` compatibility correction (2026-10-11)

A documentation-versus-implementation review found a false negative in the earlier strict SSE parser: LM Studio's [official streaming event catalog](https://lmstudio.ai/docs/developer/rest/streaming-events) includes `prompt_processing.start`, `prompt_processing.progress` and `prompt_processing.end` **on an otherwise healthy, already-loaded chat stream**. The previous parser rejected them as unknown events, incorrectly treating a benign generation as unsupported.

The draft parser now permits **at most one correctly ordered prompt-processing section** between `chat.start` and `message.start`, validates that progress values are numeric, finite, **0–1** (not booleans or NaN), and requires `prompt_processing.end` before message generation. Missing/duplicate/out-of-order events fail closed. **Model loads, reasoning, tool calls, errors and unauthorized content remain unconditionally rejected**, including model loads inserted within prompt processing.

Added 7 independent parser tests and 1 signed-receipt-bridge compatibility test, raising the source-only acceptance set to **118 cases** (36 native SSE, 14 raw SSE bridge, 36 paired synthetic signatures, 17 host GGUF correlation, 15 Linux socket lineage). Fleet Commander verified exact committed source at `dd48028c6df10e8d74b239c03ce5d98440cb02d2` on x1-370: **118 tests passed, strict Ruff passed, no live model requests**. Await separate exact-head hosted CI verification.

A passing result still proves only structural **gateway-side** model instance and test-receipt consistency; the provider has not issued real worker-specific request/owner receipts and the observer has no signing authority.

## Offline native-stream ↔ synthetic signed-receipt bridge

The second narrowly scoped check in this draft is `scripts/join_native_stream_worker_receipt.py`. It **reparses raw, bounded SSE bytes inside the join trust boundary** — a caller cannot manufacture a Python `StreamEvidence` object and have it accepted without parsing a real stream. This also forces unexpected `model_load.*`, truncated/incomplete or tool/reasoning SSE events to fail at the join itself.

The signed pair protocol inherited from [PR #314](https://github.com/scottjoyner/auto-assist/pull/314) is strengthened to require an **exact `model_instance_id` in both independently signed worker and gateway receipt payloads**. The bridge compares that matched instance with the native SSE `chat.start`/`chat.end` selection, and additionally compares:
- An externally expected opaque client `request_id` to both signed gateway/worker request IDs; it does **not** confuse LM Studio's stored `response_id` with client authentication.
- LM Studio's native SSE `response_id` to the signed gateway response ID.
- The single bounded simple SSE message's `SHA256(UTF8(message_content))` to the identical worker and gateway signed output digest.
- All existing cryptographic signature, nonce, owner digest, boot/PID/start-tick/GGUF, terminal completion, source freshness and known-request-replay checks.

Even a passing three-way bridge returns only `NATIVE_STREAM_AND_TEST_RECEIPTS_CONSISTENT_ONLY` with **`producer_public_keys_independently_trusted=false`**, **`physical_worker_process_execution_attested=false`**, **`ingress_owner_binding_attested=false`**, **`can_renew=false`**, **`can_dispatch=false`**. This does **not** upgrade an unsigned gateway stream into a real independently attested physical worker execution.

**New negative tests:** 13 offline cross-contract cases check wrong streamed model instance, response-ID mismatch, worker output mismatch, owner/request-ID confusion, bad signatures, stale/replayed requests, boot mismatch, and attempts to submit a forged parsed object or bypass JIT detection. Two new signed-worker tests require both roles to name the same valid model instance. CI now runs **110** synthetic tests total (29 native SSE + 13 bridge +36 signed pair +17 process evidence correlation +15 socket lineage).

**Source results:** x1-370 fetched the exact latest committed source into `/tmp` and verified **36/36** signed pair tests + **29/29** SSE tests + **13/13** cross-bridge tests, Ruff clean; the 17/17 and 15/15 inherited tests had separately passed on the parent and are now part of the consolidated hosted lane. There was **no inference, user prompt, real output capture, credentials, key provisioning, physical worker proof or Neo4j/NAS mutation**.

## Actual physical acceptance remains blocked

A useful future operator-authorized benign canary could collect one LM Studio native SSE stream and show:
1. Before requests, an approved model instance is **already LOADED** and resource admission, per-user identity and permission are in force; JIT loading disabled by operator-approved config.
2. Stream `chat.start` and `chat.end` select the same loaded instance, no model_load/errors, bounds are enforced and response is complete.
3. Independently trusted worker-side source records the **same** request ID/nonce and actual physical boot/PID/start ticks/GGUF digest, and signs output after complete inference. Public LM Studio's documented native stream does not currently provide this step, nor does `loaded_instances.id`.
4. Authenticated gateway independently binds user/request ownership and signs the exact worker receipt, using keys established by independent trusted custody and a durable request replay ledger.
5. Only then evaluate source-to-owner binding, operator approval, live schema migration and strict-offline behavior. **Do not auto-load a model or send unauthorized inference to obtain missing PID telemetry**.

Other pending release gates: 3 Neo4j uniqueness constraints, separate physical/gateway/operator key custody and revocation, archived signed source evidence, expired canonical 643 renewal by explicit authorized operator only, legacy unsigned retirement, [Auto-Router owner receipts #37](https://github.com/scottjoyner/auto-router/issues/37), and full AssistX CI repair. Remain draft.
