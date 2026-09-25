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
The runtime telemetry sidecar below joins speculative acceptance, prefill/decode
counters, cache reuse, VRAM/utilization, and point-in-time power where the
backend exposes trustworthy evidence. Missing metrics remain null rather than
being guessed.

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

## Runtime telemetry identity + sidecar

Phase 1 now requires runtime telemetry for every matrix policy. A trial is not
eligible for counterfactual selection unless the telemetry preflight and
postflight both prove the exact runtime identity.

Each policy declares environment-variable names for:

- the read-only telemetry sidecar URL;
- the expected exact runtime revision;
- the expected SHA-256 of the runtime process command line.

The reference sidecar is:

~~~bash
PYTHONPATH=src python scripts/assistx_runtime_telemetry_sidecar.py
~~~

It serves only:

~~~text
GET /healthz
GET /v1/telemetry/snapshot?trial_id=<trial>
~~~

POST, PUT, and DELETE return 405. It never signals, starts, stops, loads,
unloads, or reconfigures the observed runtime.

A snapshot binds:

~~~text
node_id
model_handle
backend
quantization
speculation
runtime_revision
sha256(/proc/<runtime-pid>/cmdline)
runtime process start time
~~~

The runner validates that identity against the matrix and operator-provided
expected revision/hash before it sends the inference request. It takes a second
snapshot afterward and rejects the evidence if the process restarted, the
launch command changed, or any cumulative counter regressed.

### llama.cpp telemetry adapter

When the runtime is llama-server, start it with its Prometheus metrics endpoint
enabled and set:

~~~text
ASSISTX_TELEMETRY_LLAMA_METRICS_URL=http://127.0.0.1:<port>/metrics
~~~

The reference sidecar maps llama.cpp cumulative metrics into the AssistX
contract:

~~~text
prompt_tokens_total                   -> prefill_tokens
prompt_seconds_total                  -> prefill_seconds
prompt_tokens_cached_total            -> cache_reused_tokens
tokens_predicted_total                -> decode_tokens
tokens_predicted_seconds_total        -> decode_seconds
spec_decode_num_draft_tokens_total    -> spec_proposed_tokens
spec_decode_num_accepted_tokens_total -> spec_accepted_tokens
spec_decode_num_drafts_total          -> spec_verification_steps
~~~

It also captures the available prompt/decode throughput and queue gauges.

The replay request asks llama.cpp for streamed usage/timing information. The
result therefore records per-request prompt/decode counts and draft_n /
draft_n_accepted when the runtime returns them. Process-wide telemetry deltas
are cross-checked against those exact request values. If another request touched
the same process and changed the counters, the evidence is marked invalid
instead of being attributed to the benchmark turn.

### AMD Linux GPU gauges

If the host exposes the relevant DRM sysfs files, configure:

~~~text
ASSISTX_TELEMETRY_DRM_DEVICE_SYSFS=/sys/class/drm/cardN/device
~~~

The sidecar reads, when available:

- current VRAM usage from mem_info_vram_used;
- GPU utilization from gpu_busy_percent;
- board/device power from hwmon/*/power1_average.

Missing gauges remain null; they are never synthesized.

The before/after join preserves raw gauge samples and derives only metrics that
have real counters:

~~~text
spec_acceptance_rate
prefill_tokens_per_second
decode_tokens_per_second
verification_tokens_per_second
joules_per_decode_token
cache_reuse_rate
~~~

Power is currently a point-in-time gauge, not integrated energy. Therefore
joules_per_decode_token remains null unless a future adapter supplies a real
cumulative energy_joules counter.

### Operator bootstrap

The example environment file for one R9700 Q4+DFlash endpoint is:

~~~text
examples/assistx-inference-policy-experiment/telemetry-sidecar.env.example
~~~

The critical bootstrap sequence is:

1. start the intended benchmark-only runtime;
2. record its exact source/build revision;
3. enable the runtime metrics endpoint;
4. start the read-only telemetry sidecar with the runtime PID;
5. fetch one telemetry snapshot and copy its launch_config_sha256 into the
   matching *_LAUNCH_SHA256 expectation;
6. compile the replay plan;
7. execute only after the exact revision and launch hash are frozen.

The launch hash is not a claim that two different binaries are equivalent. The
runtime revision and process command-line hash are separate evidence fields.

## 32K / 128K long-session soak

The long-session slice is implemented as a separate resumable runner rather
than a loop around the one-turn benchmark:

~~~bash
PYTHONPATH=src python scripts/run_assistx_inference_session_soak.py \
  --profiles examples/assistx-inference-policy-experiment/session-soak.profiles.json \
  --profile-id assistx-32k-soak-v1 \
  --matrix examples/assistx-inference-policy-experiment/matrix.soak.json \
  --policy-id r9700-rocm-q4-dflash-32k-c1-soak \
  --mode stable_prefix \
  --plan-out /tmp/assistx-soak-stable-plan.json
~~~

Planning is still the default. No inference or telemetry request occurs until
--execute is supplied.

### Two session modes

stable_prefix keeps one large immutable AssistX-style system context and sends
only the current synthetic turn after it. This isolates repeated prefix-cache
reuse.

growing_prefix starts with a smaller immutable context and appends every prior
synthetic user/assistant turn. This stresses growing-prefix cache reuse,
speculative rollback, long-lived KV state, and session memory behavior.

Both modes:

- use the same immutable retention marker and all-false authority boundary;
- rotate coding, JSON/tool-shaped, reasoning, and prose turns;
- require a unique TURN-NNNN-OK marker on every response;
- run stronger retention canaries on turn 1, every tenth turn, and the final
  turn;
- require the model to recover the immutable retention marker and
  ADVISORY-ONLY from the long prefix on canary turns;
- take telemetry snapshots before and after every turn;
- cross-check process metrics against exact request timings;
- stop immediately if telemetry attribution fails or the runtime process
  identity changes.

### Dedicated soak matrix

The soak matrix is intentionally separate from the phase-1 8K matrix:

~~~text
examples/assistx-inference-policy-experiment/matrix.soak.json
~~~

It declares Qwen3.8-27B Q4 policies for:

~~~text
x1-370 / Vulkan / {none,MTP,DFlash} / {32K,128K}
R9700 / ROCm   / {none,MTP,DFlash} / {32K,128K}
~~~

Every row is concurrency=1, telemetry-required, and context-specific. The plan
compiler refuses to run a 32K or 128K profile against a runtime whose declared
context is smaller than the profile target.

### Resumable execution

A physical 100-500 turn run writes each turn to append-only JSONL and updates a
hash-bound checkpoint after the turn completes:

~~~bash
PYTHONPATH=src python scripts/run_assistx_inference_session_soak.py \
  --profiles examples/assistx-inference-policy-experiment/session-soak.profiles.json \
  --profile-id assistx-32k-soak-v1 \
  --matrix examples/assistx-inference-policy-experiment/matrix.soak.json \
  --policy-id r9700-rocm-q4-dflash-32k-c1-soak \
  --mode growing_prefix \
  --turns 100 \
  --plan-out /tmp/assistx-soak-growing-plan.json \
  --execute \
  --results-out /tmp/assistx-soak-growing.jsonl \
  --summary-out /tmp/assistx-soak-growing-summary.json \
  --checkpoint-dir /tmp/assistx-soak-checkpoints
~~~

Resume uses the exact same command plus --resume. A resume is rejected if the
profile hash, policy hash, session mode, or requested turn count differs from
the checkpoint. A fresh run also refuses to overwrite an existing checkpoint or
result stream without explicit --overwrite.

Each completed turn uses a two-phase local journal:

~~~text
stage exact result + user/assistant continuation in checkpoint
append result row to JSONL
finalize completed_turns + growing-prefix history
~~~

If the process stops after staging but before the JSONL append, resume appends
the exact journaled result. If it stops after the append but before checkpoint
finalization, resume hashes the existing row against the journal before
finalizing it. A mismatch fails closed instead of guessing which turn is
authoritative.

Checkpoint journal writes and append-only result rows are fsync-backed before
the next phase is finalized, so the recovery sequence also covers abrupt host
or power loss rather than only clean process termination.

Growing-prefix checkpoints retain only the synthetic conversation history
needed to reconstruct the next request. The large immutable seed context is
deterministically regenerated from the frozen profile rather than duplicated in
the checkpoint.

### Session gates

The checked-in profiles default to 100 turns and allow 20-500. A completed
summary evaluates:

~~~text
request success rate
per-turn deterministic acceptance rate
retention-canary pass rate
telemetry-valid rate
TTFT p50/p95 and late-vs-early drift ratio
wall-time p50/p95 and late-vs-early drift ratio
first/last/min/max prompt tokens
cache-reuse mean
speculative-acceptance mean
first/last/max VRAM
VRAM growth and simple bytes/turn slope
per-workload-class latency, cache, and speculation evidence
~~~

The default gate requires 100% retention-canary and telemetry validity, at least
99% request success, at least 98% deterministic acceptance, no more than 2x
TTFT/wall drift, and no more than 2 GiB of observed VRAM growth when the gauge
is available.

Missing VRAM is reported as unavailable rather than fabricated. Missing latency
or correctness evidence cannot pass the corresponding gate.

### Stable vs growing comparison

After running both modes for the same exact policy/context:

~~~bash
PYTHONPATH=src python scripts/compare_assistx_inference_session_soaks.py \
  /tmp/assistx-soak-stable-summary.json \
  /tmp/assistx-soak-growing-summary.json \
  --output /tmp/assistx-soak-comparison.json
~~~

The comparison records growing-vs-stable TTFT/wall ratios plus cache-reuse,
speculative-acceptance, VRAM-growth, and latency-drift deltas. It is evidence
only and cannot promote a runtime or alter AssistX routing.

For clean cache comparisons, run each stable/growing session against a fresh
dedicated runtime process with its own frozen revision and launch hash. The
soak runner itself deliberately does not restart or clear a runtime.

## 32K-to-128K campaign gate

The paired soak can now be planned and evaluated as one immutable campaign
rather than as a collection of ad hoc operator commands.

The checked-in campaign config is:

~~~text
examples/assistx-inference-policy-experiment/campaign.32k-to-128k.json
~~~

It freezes:

~~~text
source context: 32K
target context: 128K
turns: 100
nodes: x1-370, r9700
speculation: none, MTP, DFlash
fresh stable/growing process pair: required
same runtime revision across the pair: required
same launch configuration across the pair: required
~~~

### Compile the physical 32K campaign

~~~bash
PYTHONPATH=src python scripts/manage_assistx_soak_campaign.py plan \
  --config examples/assistx-inference-policy-experiment/campaign.32k-to-128k.json \
  --matrix examples/assistx-inference-policy-experiment/matrix.soak.json \
  --profiles examples/assistx-inference-policy-experiment/session-soak.profiles.json \
  --output /tmp/assistx-soak-campaign.plan.json \
  --commands-out /tmp/assistx-soak-campaign-32k.sh \
  --evidence-dir /tmp/assistx-soak-campaign
~~~

This is planning only. It creates no network requests and starts no runtimes.

The resulting manifest currently contains six 32K candidates:

~~~text
x1-370 / Vulkan / Q4 / none
x1-370 / Vulkan / Q4 / MTP
x1-370 / Vulkan / Q4 / DFlash
R9700 / ROCm   / Q4 / none
R9700 / ROCm   / Q4 / MTP
R9700 / ROCm   / Q4 / DFlash
~~~

Each candidate has a stable-prefix and growing-prefix run, for twelve physical
32K runs total. The generated command sheet marks every run with a fresh-runtime
boundary. It deliberately does not restart, clear, load, unload, or reconfigure
the inference process.

The operator must bind each command to the matching dedicated runtime and
telemetry environment before executing it.

### Exact campaign binding

The campaign plan stores and hashes:

- campaign configuration;
- exact 32K source profile ID + SHA-256;
- exact 128K target profile ID + SHA-256;
- exact source policy ID + SHA-256;
- matching target policy ID + SHA-256;
- node/backend/quant/speculation/model signature;
- requested turn count;
- stable/growing run filenames and checkpoint locations;
- all-false authority state.

The evaluator recomputes the campaign-plan hash before accepting any summary.
Editing the plan after it was frozen invalidates evaluation.

### Advancement gate

After the physical 32K runs finish, evaluate their summaries:

~~~bash
PYTHONPATH=src python scripts/manage_assistx_soak_campaign.py evaluate \
  --plan /tmp/assistx-soak-campaign.plan.json \
  --summaries /tmp/assistx-soak-campaign/*.summary.json \
  --output /tmp/assistx-soak-campaign.evidence.json \
  --advance-commands-out /tmp/assistx-soak-campaign-128k.sh \
  --evidence-dir /tmp/assistx-soak-campaign-128k
~~~

A candidate advances only when both the stable and growing summaries prove all
of the following:

~~~text
correct summary schema
exact 32K profile ID + profile SHA
exact 32K policy ID + policy SHA
exact 32K target context
exact 100-turn completion
session gate passed
internally consistent runtime identity
same runtime revision across stable/growing
same launch-config SHA across stable/growing
different process-start identity across stable/growing
~~~

The different process-start requirement proves that the two cache/session modes
ran on distinct fresh processes while the same revision + launch hash proves
that the underlying runtime build/configuration stayed comparable.

A reused warmed process fails the gate. A changed binary or launch
configuration fails the gate. A changed soak profile fails the gate. Missing,
partial, or failed summaries fail the gate.

### Target-context command sheet

The optional --advance-commands-out file contains only the 128K stable/growing
runs corresponding to candidates that passed every 32K gate.

That file is still an operator command sheet, not an authorization mechanism.
It does not start runtimes and does not execute automatically.

After those gated 128K runs finish, close the campaign with the target-context
evaluator:

~~~bash
PYTHONPATH=src python scripts/manage_assistx_soak_campaign.py evaluate-target \
  --plan /tmp/assistx-soak-campaign.plan.json \
  --source-evidence /tmp/assistx-soak-campaign.evidence.json \
  --summaries /tmp/assistx-soak-campaign-128k/*.summary.json \
  --output /tmp/assistx-soak-campaign-128k.evidence.json
~~~

The target-context evaluator first verifies that the source advancement evidence
belongs to the same immutable campaign-plan hash. It then applies the same
fresh-process, exact-profile, exact-policy, same-revision, same-launch-config,
turn-completion, correctness, telemetry, and drift gates to each eligible 128K
stable/growing pair.

The final result names only policies with a fully completed 128K benchmark pair
under benchmark_complete_target_context_policies. This is a benchmark evidence
set, not a production admission list.

The source and target evidence outputs always record:

~~~text
production_promotion_authorized=false
routing_authority_changed=false
~~~

"Eligible" in this campaign means eligible for the next benchmark context only.
It never means admitted to production AssistX routing, runtime discovery, model
loading, claims, approvals, tools, or mutation authority.

## Next slices

1. Run the generated physical 32K campaign, the gated 128K campaign, and
   capture both immutable campaign evidence files.
2. Add task-specific evaluators for real code, tool calls, reviews, and
   project-specific long-context constraint retention.
3. Add an integrated energy sampler and runtime-specific cache/reprocess
   adapters where the backend exposes trustworthy counters.
4. Add true concurrency at 2/4/8 with trial-scoped telemetry attribution.
5. Join accepted counterfactual + completed campaign evidence to my-jev
   training/evaluation without granting the learned layer dispatch authority.

The promotion gate remains quality first: a faster policy matters only when it
clears the task-specific acceptance threshold.
