# Starred-repo intake: Harness² shadow paired-evidence trial

**Status:** research-only / NO-GO for production deployment or scheduling.  
**Upstream source:** google-research/harness2 at `6868817518876de8fd170c6a8354daca273b6191` (Apache-2.0).  
**Related:** AssistX #468, llmfit PR #469, witr PR #472, SensorGraft #64.

## Independent disposable upstream demo run (2026-11-02)

We inspected the documented demo and ran the *upstream source at exact commit* using
`PYTHONPATH=<read-only checkout> python3 -m harness2.cli.demo` on x1-370. No
pip/uv install, production service, external model, API credential, live trace,
model weights, broker, or task controller was used. The source tree and output
were in a disposable temporary directory with cleanup on exit.

| Scope | Observed |
| --- | --- |
| k=1, parallel + sequential | 2/2 demo tasks completed; 0 failures |
| k=1 | 14 executions and 16 deterministic editor calls |
| k=2, parallel + sequential, independent first run | 2/2 completed, 0 failed, 28 executions, 32 editor calls |
| k=2, independent second run | 2/2 completed, 0 failed, 28 executions, 32 editor calls |
| JSON summary reproducibility, k=2 | two summary JSON reports compared equal |
| Inference tokens or actual model quality improvement | **NOT MEASURED** |
| Upstream full benchmark execution | **NOT RUN** |

This is only evidence that the **scripted local recursion demonstrator** ran
successfully. The demo's proposer is deterministic and directly reads a simple
synthetic ledger to select answers; it is not independent blind judging or
evidence that a model improves by the reported amount. Its executions must **not**
be included in fleet generated-token utilization totals.

## Why not install the full harness

Upstream documentation states that executor network access is unrestricted
and its Codex substrate disables the sandbox. Full benchmarks can incur model
API costs; some public rubrics are retrievable online; benchmark datasets
retain independent licenses. Do not enable the full runner, broad GitHub
credentials, live executor, production plugin, auto-merge, or network access
without a separate reviewed isolation and custody design.

## New shadow evidence adapter

`scripts/experiments/harness2_paired_shadow_gate.py` takes *caller-supplied*
**aggregate observation JSON**. It never imports or executes Harness². It only
computes whether an externally collected paired-task result merits **manual
research review**. Its schema requires:

- An exact upstream Git commit SHA, 64-character SHA-256 digests for dataset,
  model, and judge, recursion mode, k (1–8), and seed.
- Baseline and candidate results for the **same task and input digest**, and
  consistent judge/model identities across every observation.
- Per-completed-task quality score (0–1), integer observed prompt/completion
  token counts and positive measured-duration value.
- An explicit evidence origin: `synthetic` or `caller_reported_measured`.
- Candidate edits restricted to `system_prompt`, `memory`,
  `tool_guidance`, and `skills`; guardrails, scripts and plugins may not be
  modified by an unapproved candidate.

Incomplete/unscored tasks are rejected instead of silently being assigned
zero-quality scores. A *research signal* requires at least 20 complete paired
tasks, mean candidate improvement >=0.05, at most 10% task regressions, no
completion loss and token/latency consumption <=1.25x baseline. Those are
**provisional, adjustable experimental heuristics**, not proven statistical
confidence or release approval. Reported input/output tokens are not verified
by this parser and must not be presented as fleet-verified usage.

It **always** returns `production_gate=HOLD`, `dispatch_allowed=false`,
`adoption_authorized=false`, and explicitly false provenance/quality
verification flags, *even for favorable results*.

To use on manually prepared synthetic JSON:

```sh
python3 scripts/experiments/harness2_paired_shadow_gate.py --input /path/to/synthetic-pairs.json
python3 -m unittest discover -s tests -p test_harness2_paired_shadow_gate.py -v
```

## Follow-up gates

1. Independently define productive task-value rubrics without exposing grader
   answers to the improver; only validated task completions count.
2. Map Hermes/PI output to real **signed and fenced** AssistX task identities;
   reject stale projections, duplicate claims, unmeasured token counts, and
   claimed model state not corroborated by physical evidence.
3. Evaluate same pinned model, seed, dataset revision, quant, context cap,
   provider and tool configuration against a fixed baseline and candidate.
4. Measure actual prompt and generated tokens, end-to-end quality,
   deliverable validation, wall-clock latency, concurrency, GPU/CPU
   occupancy, and time/resource cost **without modifying live services**.
5. Run isolated negative tests for rubric leakage, tool exfiltration, unsafe
   component edits, cancellation and restart; keep all production changes
   behind independent approvals.

**No merge, deployment, automatic weight/harness updates, or runtime/custody
re-approval is implied by these observations.**
