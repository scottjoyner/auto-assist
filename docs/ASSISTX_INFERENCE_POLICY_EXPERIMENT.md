# AssistX inference-policy replay experiment

This slice turns the existing AssistX/my-jev shadow evidence lane into an
offline inference-policy experiment. It asks: for the same frozen AssistX turn,
which already-running local inference policy is fastest while still passing the
case acceptance gate?

The result is evidence only. Nothing here changes live classification, routing,
claims, approvals, tool access, cwd, mutation authority, runtime admission,
model loading, or model unloading.

## Stack position

~~~text
live AssistX intent
      |
      +--> existing authoritative handling
      |
      +--> my-jev shadow evidence
                 |
                 v
      export_my_jev_policy_shadow.py
                 |
                 v
      frozen replay cases
                 |
                 v
      explicit inference-policy matrix
                 |
                 v
      offline replay results + counterfactual summary
~~~

There is deliberately no arrow from the replay summary back into the live
allocator.

## Phase 1

The first runnable phase is intentionally small:

- anchor family: Qwen3.8-27B
- nodes: x1-370 and r9700
- backends: Vulkan on x1-370, ROCm on r9700
- context: 8K
- concurrency: 1
- speculation: none, MTP, DFlash
- quantization: Q4 enabled first; Q8/FP16 rows are staged but disabled until
  matching already-loaded endpoints exist

The runner rejects any policy that declares model loading or non-observational
execution authority.

## Evidence contract

Every trial binds a frozen case hash to a frozen policy hash. Results preserve
the case/policy hashes, requested and returned model identity, model-match
result, HTTP status/error, TTFT, wall time, decode window, usage-derived token
counts when available, acceptance result, and output hash/length.

Generated text is stored only when --include-output is explicitly requested.

The generic OpenAI-compatible stream does not expose every runtime metric.
Speculative acceptance, verification throughput, prefill tok/s, VRAM, power,
and cache telemetry must be joined from a runtime-side observer in a later
slice rather than guessed.

## Frozen AssistX traces

Export existing shadow rows:

~~~bash
PYTHONPATH=src python scripts/export_my_jev_policy_shadow.py \
  --output /tmp/assistx-shadow.jsonl \
  --limit 100
~~~

Those exports may contain raw user/session text. Keep them local.

Compile an immutable trial plan without inference requests:

~~~bash
PYTHONPATH=src python scripts/run_assistx_inference_policy_experiment.py \
  --shadow-export /tmp/assistx-shadow.jsonl \
  --matrix examples/assistx-inference-policy-experiment/matrix.phase1.json \
  --plan-out /tmp/assistx-policy-plan.jsonl
~~~

Shadow-exported rows are intentionally unscored. Historical verification stays
as baseline evidence but is not treated as proof that a new candidate is
correct.

For deterministic smoke testing:

~~~bash
PYTHONPATH=src python scripts/run_assistx_inference_policy_experiment.py \
  --cases examples/assistx-inference-policy-experiment/cases.example.jsonl \
  --matrix examples/assistx-inference-policy-experiment/matrix.phase1.json \
  --plan-out /tmp/assistx-policy-plan.jsonl
~~~

## Endpoint setup

Each matrix row references an environment variable instead of embedding an
endpoint. The endpoint must already represent the declared runtime policy.

Enabled Q4 first-pass variables:

~~~text
ASSISTX_BENCH_X1_Q4_NONE_URL
ASSISTX_BENCH_X1_Q4_MTP_URL
ASSISTX_BENCH_X1_Q4_DFLASH_URL
ASSISTX_BENCH_R9700_Q4_NONE_URL
ASSISTX_BENCH_R9700_Q4_MTP_URL
ASSISTX_BENCH_R9700_Q4_DFLASH_URL
~~~

The harness appends /v1/chat/completions when necessary. If an endpoint
requires a bearer token, add api_key_env to the policy row; the secret remains
in the environment.

The runner does not start a server, load/unload a model, or change speculation
settings. Runtime setup remains an explicit operator action.

## Execute

~~~bash
PYTHONPATH=src python scripts/run_assistx_inference_policy_experiment.py \
  --cases examples/assistx-inference-policy-experiment/cases.example.jsonl \
  --matrix examples/assistx-inference-policy-experiment/matrix.phase1.json \
  --plan-out /tmp/assistx-policy-plan.jsonl \
  --execute \
  --results-out /tmp/assistx-policy-results.jsonl \
  --summary-out /tmp/assistx-policy-summary.json \
  --baseline-policy-id x1-370-vulkan-q4-none-8k-c1
~~~

--execute is required before any network request is made.

Phase 1 rejects concurrency values other than 1. True 2/4/8 concurrency belongs
in a separate load-generator slice so the evidence cannot claim concurrency
that was actually executed sequentially.

## Acceptance and counterfactuals

Cases currently support deterministic required_terms and json_required_keys
checks. A case with no acceptance rule is unscored and cannot become the
counterfactual winner.

For each case the summary records selected_policy_id, selected_policy_score,
best_observed_policy_id, best_observed_policy_score, baseline/best wall time,
and baseline-to-best speedup.

The phase-1 score is intentionally simple:

~~~text
accepted candidate: 1000 / wall_ms
failed acceptance:  0
unscored candidate: null
~~~

This is an experiment-local ranking only. It is not a production routing score
and is never written back into the AssistX allocator.

## Next slices

1. Add task-specific evaluators for code, tool calls, reviews, and long-context
   constraint retention.
2. Join runtime telemetry for prefill, speculative acceptance, verification
   throughput, VRAM, cache reuse/reprocess, and power.
3. Add 32K/128K long-session soak with stable/growing prefixes.
4. Add true concurrency at 2/4/8.
5. Join counterfactual evidence to my-jev training/evaluation without granting
   the learned layer dispatch authority.

The promotion gate remains quality first: a faster policy matters only when it
clears the task-specific acceptance threshold.
