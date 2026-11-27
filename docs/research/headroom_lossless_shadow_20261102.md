# Headroom v0.42.0 shadow evaluation — lossless agent mode

**2026-11-02 · x1-370 · research-only / production HOLD.**

## Why evaluated

The public `scottjoyner` starred list still contained **1,068**
repos on November 2 (page 11: 68; page 12: 0).
Following the safety findings for
[RTK OSS intake PR #483](https://github.com/scottjoyner/auto-assist/pull/483),
we evaluated [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom)
to ask whether its **lossless agent-mode densification** can reduce
coding-agent context without silently deleting signed-witness failure
evidence or changing the tool output the model sees.

Upstream Headroom v0.42.0 is Apache-2.0; its README explicitly
describes an **anonymous telemetry beacon enabled by default**.
The upstream `densify(...)` API selects `mode="agent"`,
sets `verify_lossless=True`, prevents CCR removal markers,
disables ML text compression, and reverts unverifiable transforms
rather than emitting changed data. The pilot intentionally selects
this mode instead of Headroom proxy, wrap, MCP server, shared memory,
ML model download or tool hooks.

## Artifact, environment and real execution

- Exact PyPI wheel:
  `headroom_ai-0.42.0-cp310-abi3-manylinux_2_28_x86_64.whl`,
  publisher-reported SHA-256 **`fbb3ceb8f6c3f208edcc3aad43c0e03e0db45636d9d2061525ad0d0a166da146`**.
  The wheel was downloaded separately and matched this SHA-256
  immediately before the test.
- Minimal staged Python wheels:
  `opentelemetry-api==1.45.1`, `tiktoken==0.12.0`,
  `pydantic==2.12.5`, `rich==14.2.0`,
  `click==8.3.3` and their transitive dependencies.
  These were downloaded on the host for an isolated ephemeral
  test, and installed **offline, without resolving dependencies**
  inside a disposable container. Unlike the Headroom wheel,
  the full dependency wheel set is not individually hash-pinned
  or locked; this is not a supply-chain attestation.
- Runtime was a disposable `python:3.12-slim-bookworm` Docker
  container with **network disabled**, read-only root,
  nonroot UID 65534, zero Linux capabilities, no-new-privileges,
  1 CPU, 1.2 GiB RAM, 48 PIDs, bounded temporary filesystem,
  and read-only mounts of the input script and wheel directory.
  `pip --no-index --no-deps --target /tmp/lib`.
  No host Python installation, production repository mount,
  live trace corpus, external proxy, user messages,
  agent hooks or production service changes. All workspaces
  were deleted at container exit.
- `HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`,
  `HEADROOM_TELEMETRY=off`, `DO_NOT_TRACK=1`.
  The research script requires every environment flag and
  explicit `--execute-synthetic`; by default it does not import
  Headroom at all. **The script itself does not attest actual
  container network isolation**; the Docker invocation enforced
  `--network none` in the measured run.
- Model label `gpt-4o` is for library routing/token estimation
  only; **no model was called or generated any tokens**.

## Critical offline behavior

With all offline/telemetry flags present, the library's tokenizer
still attempted to retrieve the `o200k_base.tiktoken` file
from `openaipublic.blob.core.windows.net`. Docker's
`--network none` blocked the attempt, and the library logged a
fallback to estimated token counts. Content detection also fell
back from unavailable ONNX to pure Python.

**Observed fact:** a network attempt occurred and was denied.
**Not established:** that private data was sent, that telemetry
transmitted, or that provider token counts were correct.
No network egress was enabled in the runtime. Thus library
`tokens_before/after` fields are **estimates**, not verified
inference input-token consumption.

## Exact committed synthetic output

All cases include synthetic
`FATAL_TRACE_HASH=8a593fac2bca17c4` and
`src/retrieval.rs:87`, representing a deliberately crafted
fictional failure. None comes from actual trace custody,
code execution receipts or personal user data.

`scripts/experiments/headroom_lossless_shadow.py` ran at commit
`87b724097e53e10925b7a59faf124983d4a0d6fe`
with the exact pinned wheel:

| Fixture | Source bytes | Output bytes | Real byte saving | Failure marker | Upstream lossless |
| --- | ---: | ---: | ---: | --- | --- |
| Pretty structured JSON | 99,118 | 99,118 | **0%** | Retained | True |
| NDJSON | 78,375 | 78,375 | **0%** | Retained | True |
| Cargo test output | 11,647 | 11,647 | **0%** | Retained | True |
| Application logs | 28,577 | 28,577 | **0%** | Retained | True |

Every output was **byte-for-byte unchanged**, including
the marker and source location. The library reported one
reverted message for application logs, and none for the
other three cases. The exact byte-count checks and full
source/output SHA-256 comparisons independently confirm
that no content changed. **This is evidence preservation,
not a compression win.**

Earlier exploratory execution produced the same no-saving
behavior for similar log, Cargo and JSON fixtures.
No latency speedup, cost reduction, task completion
gain, cache improvement, or fleet utilization uplift
was observed or claimed.

## Tests and controls

A tiny stdlib-only script generates four fixed synthetic cases,
imports Headroom **only with an explicit switch**,
requires offline/telemetry env vars, and emits numeric
research receipts (sizes, digests, failure marker flags and
estimated tokens). It never prints synthetic content or
literal marker strings. Changes that cannot be independently
decoded and re-compared to the original are **not promoted**:
a library claim of lossless plus marker preservation alone
is insufficient for claim evidence.

`tests/test_headroom_lossless_shadow.py` focuses on:
missing/corrupted failure markers and source location;
changed output with upstream `lossless=True`;
bad sizes/timings/tokens/types; offline/telemetry defaults;
no default dependency import/execution; no silent agent
hook admission; deterministic fixture content; and SHA drift.

Exact local focused result on x1-370 at the code head above:
**20/20 tests passed**, AST parsing passed.

## Additional successful-output control (no failure markers)

To determine whether the failure-bearing fixtures were simply being
protected because of diagnostic severity, a second **real binary**
execution was run on the same verified Headroom 0.42.0 wheel, staged
minimal dependencies and read-only, no-network Docker policy
(`HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`,
`HEADROOM_TELEMETRY=off`, `DO_NOT_TRACK=1`).

This controlled *exploratory* run tested four deliberately benign,
highly repetitive and much larger synthetic payloads using
`headroom.densify(..., protect_recent=0,
min_tokens_to_compress=25)`. All four preserved the entire input
byte for byte; the library reported `lossless=True` and
estimated zero tokens saved. It again attempted an offline-blocked
tokenizer fetch and fell back to estimated tokenization.

| Benign synthetic fixture | Original bytes | Output bytes | Actual byte saving |
| --- | ---: | ---: | ---: |
| Pretty JSON, 650 rows | 188,409 | 188,409 | **0%** |
| NDJSON, 650 rows | 153,939 | 153,939 | **0%** |
| Cargo-success, 750 tests | 29,288 | 29,288 | **0%** |
| Successful application logs, 750 rows | 51,639 | 51,639 | **0%** |

The benign-control generator was kept ephemeral and is not yet an
independently packaged, committed test fixture. These values are
**measured exploratory observations**, not reproducible CI benchmarks
or general evidence that upstream Headroom cannot compress other
content. The four failure-bearing cases above remain the executable
committed validation set.

This result narrows the immediate recommendation: **no measured
benefit in agent-lossless mode even on repetitive, success-only
outputs under this constrained environment.** Do not enable
context compression just to increase nominal token-savings reports.

## Decision

**HOLD — no fleet rollout.** Headroom's conservative lossless mode
was safe on these controlled failure fixtures but did not reduce
the actual output bytes. Do not interpret that as universal
ineffectiveness or substitute estimated token counts for
real API prompt-token telemetry.

A future useful pilot could use a pinned, complete
dependency lock and tokenizer cache within a verified sandbox,
then test a broader corpus of truly successful non-sensitive
coding tasks with explicit full roundtrip proof and source
evidence retention. Measure **real provider token counts and
task success**, not just output bytes. Compare against
RTK OSS shadow results and uncompressed controls before
introducing any optional agent-context optimization.
Never enable telemetry, wrap/rewrites, a second proxy,
live trace compression, or automatic graph authority on
the basis of these synthetic results.
