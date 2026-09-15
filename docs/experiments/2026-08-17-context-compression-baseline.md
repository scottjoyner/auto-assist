# Context Compression Baseline — 2026-08-17

Issue: `#26`
Branch: `agent/context-compression-baseline`

## Hypothesis

Before adopting Headroom or another learned/content-aware compressor, AssistX should establish a lossless baseline that is nearly free to run. Any upstream dependency must beat that baseline on representative prompts while preserving correctness and evidence fidelity.

## First baseline

Added a conservative context optimizer with one enabled transformation:

- valid JSON / tool JSON → minified JSON using Python stdlib serialization.

Everything else is identity by default. This is intentional: user instructions, prose, logs, code, diffs, and unknown content types must not be changed until content-specific retention tests exist.

The result records:
- original character count;
- optimized character count;
- reduction ratio;
- whether content changed;
- strategy name.

## Tests

The first tests verify:
- pretty JSON becomes smaller;
- parsed values remain exactly equivalent;
- invalid JSON is returned unchanged;
- unknown content types remain unchanged;
- empty input has a stable zero reduction ratio.

## What this proves

If CI passes, it establishes a deterministic, lossless floor for JSON-heavy tool traffic. Headroom must then show incremental value beyond plain minification, especially on logs, diffs, nested tool output, and RAG bundles.

## Next bake-off

For a fixed corpus, compare:

1. identity/raw payload;
2. stdlib lossless baseline;
3. Headroom compression;
4. Headroom CCR/retrieval path when details are omitted.

Measure:
- characters and tokenizer-estimated input tokens;
- compression time;
- TTFT and total latency on a real local endpoint;
- task correctness using `#24` gates;
- exact ID/number/path retention;
- retrieval-on-demand behavior;
- prompt/prefix cache impact.

## Guardrail

The original payload remains the evidence artifact. Optimized content is a derived representation only; it must never overwrite or become the sole copy of source evidence.
