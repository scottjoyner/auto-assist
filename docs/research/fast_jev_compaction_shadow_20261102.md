# Starred fast-jev-compaction — evidence-safe shadow audit for AssistX

**Date:** November 2, 2026 (America/New_York)  
**Status:** research draft, **production HOLD**, no external model calls or real session modification.

## Why this starred repository

Reviewed MIT-licensed
[tamaratran/fast-jev-compaction](https://github.com/tamaratran/fast-jev-compaction),
exact source commit
`e3f262a7f4d42bd8dd32ced30d26176f7cb545b0`,
NPM package version `0.2.0`. Its TypeScript reference library
tracks `tool_use_id` call/result pairs and, for older eligible tool
events, chooses: keep in full, keep call but truncate result, or
remove both. It intentionally preserves ordinary user/assistant
message text unchanged. The normal implementation asks **TypeSafe
Jev** questions using `api.typesafe.ai/v1/systemone`, potentially
transmitting full session context. It is a **Claude Code plugin**,
not an AssistX-native security/custody provider.

**Do not configure real session forwarding to Jev** without
separate consent, privacy review, provider allowlist, spend controls,
and trusted data classification. Do not compact live SensorGraft
traces, immutable receipt archives or operator-policy evidence.

## Verified upstream reference

Downloaded the pinned upstream tarball into a disposable directory
on `x1-370`, SHA-256 of downloaded archive
`4b1de046ee17f93079968c0865a12bf94fa264f007b2527c0a11eb6794550e77`
(local reproducibility digest, **not an independent publisher
signature**).

Staged NPM dependencies with `npm ci --ignore-scripts` only in
that temporary directory. Executed the actual upstream Vitest
suite in `python:3.12-slim-bookworm` with the host Node executable
mounted read-only and `--network none`, `--read-only`,
nonroot UID 65534, one CPU, bounded memory and tmpfs,
**`--no-cache`** to prevent Vitest writing to its read-only
source mount:

- `tests/fast-jev-compaction.test.ts` — **23/23 PASS**
- `tests/hook.test.ts` — **6/6 PASS**
- Upstream total — **29/29 PASS**

The initial no-cache omission let Vitest run 29 tests but fail
during final cache writing; the subsequent `--no-cache` run
passed fully. No TypeSafe provider call or live app hook occurred.

## Research code added to AssistX

`src/assistx/fast_jev_compaction_shadow.py` is **standard-library
only**. It does not perform Jev scoring or automated compaction.
Instead, a caller can present a *synthetic original transcript*,
a proposed compacted version and an **externally supplied**
action decision map, and it performs a fail-closed,
side-effect-free integrity check:

- No changed, invented, reordered or deleted nonempty
  **user or assistant prose**; full text is compared in
  memory, never printed.
- Each remaining tool-result ID must still identify exactly
  one original tool use. No unknown/duplicate tool IDs,
  unpaired calls, altered inputs, tool types, error flags
  or injected messages.
- `keep`: both original tool call and result remain
  byte-for-byte in their text fields.
- `drop_result`: both messages remain paired, and their
  text must match **exactly** fast-jev's documented
  head-plus-marker truncation; naturally short results
  remain unchanged.
- `drop_call`: original call **and** result must disappear
  together; no orphan may survive.
- First-message and recent-message tool evidence is pinned.
  Error-bearing results, non-allowlisted tools (including
  arbitrary shell commands), and explicit `protected_ids`
  are always pinned, regardless of a Jev preference.
  Only `Read`, `Grep` and `Glob` are *potentially*
  compactable in this synthetic exercise; allowlisting a
  tool **does not** mean its content is non-sensitive.
- Strict bounded JSON-compatible fields and max
  2 MB / 256 messages / 128 pairs. Inputs are
  never logged, stored, sent to external services,
  scored by a model or persisted in the graph.
- `scope` must be `synthetic_fixture_only`.
  The source classification and protected-ID map are
  **caller-provided and unauthenticated**, so every
  resulting report explicitly remains
  `production_gate=HOLD`. The test does not
  authorize using the guard on live conversations.

`tests/test_fast_jev_compaction_shadow.py` exercises
instruction mutation, tool/result orphan prevention,
duplicate IDs, incorrect truncation, code/data injection,
Bash/error preservation, explicit protected evidence,
non-JSON values and source/budget limits.

## Differential against the *real* upstream library

`research/fast_jev_offline_reference_fixture.mjs` hash-locks
five upstream files:
`compact.ts`, `state.ts`, `request.ts`, `types.ts`,
`index.ts` to byte digests measured from the pinned commit,
then imports upstream `compact()` with **a local mocked
JevAsker**. The test uses only fabricated strings:

- User: `Never edit src/generated. Keep any signed evidence.`
- Old `Read` result: 1,200 synthetic `A` characters
- Older `Grep` result: 1,300 synthetic `B` characters
- A `Bash` failure that must survive intact
- Final user/assistant prose that must remain unchanged

Fake decision policy: drop `read_a` call and result,
truncate `grep_b` result, keep `bash_c`.
No external Jev model, local LLM, inference router or
real user data was touched.

The **actual upstream `compact()` output** was passed to
the exact AssistX research auditor, which returned
**PASS**, with:

| Synthetic measurement | Result |
| --- | ---: |
| Original messages | 10 |
| Proposed messages | 8 |
| Removed call/result pairs | 1 |
| Truncated result pair | 1 |
| Protected result pairs kept intact | 1 |
| Original serialized bytes | 6,073 |
| After serialized bytes | 1,614 |
| Synthetic bytes removed | **4,459** |
| User/assistant prose rewritten | **No** |
| Real model/token usage measured | **No** |
| Production trace/graph writes | **No** |

**These are JSON serialized byte counts**, not tokenizer-derived
token savings, latency speedup or an estimate of actual LLM
capacity recovered. A synthetic example is not a
representative chat workload.

## Why this does not prove production safety

- A fake Jev scorer provides no relevance-quality evidence.
  Critical tool output may be necessary to resume work
  even if its name is `Read` or `Grep`.
- Upstream's normal client sends state to a **third-party
  endpoint** and may consume paid/limited API capacity;
  no free eligibility or data handling terms were
  independently verified here.
- The original tool metadata and separate protected-ID
  labels are user-provided claims. There is no independent
  SGNC receipt classification or authorization
  attestation in this module.
- Byte reduction does not prove shorter model token context,
  improved answer correctness, lower cost or user utility.
- The harness does not run Agent/Hermes/AssistX production
  prompts, inspect private traces or touch the graph.

## Smallest future gates

1. Provide a trusted source-classifier that **excludes all
   signed SGNC evidence, human approvals, security
   decisions, secrets and operator instruction history**,
   not merely a tool-name allowlist.
2. Test positive and negative replay/resume scenarios with
   synthetic but realistic task histories. Score exact
   constraint recall, patch correctness and no lost
   approvals against an un-compacted baseline.
3. Benchmark tokens with an **actual target model's
   tokenizer** in an offline local setup; compare
   prompt tokens and successful task outcomes, not
   characters alone. Measure both CPU-time for
   scoring and token budget recovered.
4. If using Jev externally, require a separate
   approved provider, DLP/privacy gates, hard free/
   spend budget, safe network egress and immutable
   audit of which context was transmitted.
5. Any later production use must have real graph
   authority separation, trustworthy protected-ID
   source and fail-open to **original unchanged
   context** on any uncertainty.

**Decision:** keep this as a research-only **post-compaction
auditor**. Do not install the Claude plugin, add a paid
external Jev dependency or modify live session/traces.
