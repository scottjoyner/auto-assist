# Starred RTK OSS v1.1.0: source evidence retention before agent output compression

**Date:** 2026-11-02 · **Host:** x1-370 · **Research-only / production HOLD**.

## Candidate and threat model

Selected starred [rtk-ai/rtk](https://github.com/rtk-ai/rtk) to reduce
coding-agent *tool-output* bytes, not to change model decoding speed.
The upstream license boundary matters: the repository advertises
`rtk-oss` **Apache-2.0** filters/hooks without the BUSL-1.1 engine;
the full `rtk` executable links BUSL-1.1 `rtk-engine`, and the
optional `rtk-proxy` is also BUSL-1.1. Only the OSS release binary was
tested. **No hooks, plugins, auto-rewrites, LLM proxy, daemon, AI agent
or production trace ingestion were enabled.**

The risk is compressing away the very text that a coding agent,
operator or signed-witness audit needs to diagnose a failed test:
precise failure markers, source locations, trace hashes, error messages,
and exit status. Compression can preserve an overall "FAILED" summary
while silently omitting its evidentiary reason. Neither output
reduction nor a filter process's exit code can prove task success.

## Trusted executable and sandbox

Official release `v1.1.0` (October 25, 2026), archive
`rtk-oss-x86_64-unknown-linux-musl.tar.gz`:

`SHA256 4dec94a64035e08189ae82fd5408fe4ae9ae27a665fc07e3e3ec3c4c2d8e8a3e`

Confirmed this against the GitHub release's own SHA-256 metadata.
Checked every TAR member for absolute paths, `..` components,
symlinks, hardlinks and special-device entries **before extraction**.
The archive contained an `rtk` binary and `LICENSE` file.

All RTK executions were in disposable `python:3.12-slim-bookworm`
containers with **`--network none`, read-only root, unprivileged UID
65534, zero capabilities, no-new-privileges, 1 CPU, 256 MiB RAM,
32 PIDs, bounded 24 MiB temporary filesystem**, and read-only mounted
binary. `HOME` and XDG paths were redirected to temporary paths.
No git repo mount, production data, writable host volume or
unrestricted command execution. The binary was invoked **only**
as `rtk pipe --filter <known-name>` with synthetic stdin.

## Reproduced losses and exact end-to-end gate result

Input is synthetically generated successful tests plus a deliberately
placed failed-test message carrying the immutable marker
`FATAL_TRACE_HASH=8a593fac2bca17c4`. The marker is **not** a live
signature or actual incident.

| Synthetic format/filter | Original bytes | Compacted bytes | Byte reduction | Required marker | Research candidate |
|---|---:|---:|---:|---|---|
| Cargo test, `cargo-test` | 3,953 | 40 | 98.99% | **LOST** | **HOLD** |
| Pytest, `pytest` | 4,877 | 149 | 96.94% | **LOST** | **HOLD** |
| Application log, `log` | 11,012 | 241 | 97.81% | Preserved | Advisory only |

The upstream *pipe filter itself* returned exit code **0** even for
synthetic failed test output. The standalone gate therefore requires a
**separately supplied source command exit status**; it never interprets
the RTK pipe exit code as the original test command's status. That
source status is **caller-supplied, not independently attested**.

Earlier exploratory public synthetic outputs also showed a
`git-log` filter reducing a 180-line log to four entries and a
`[+176 lines omitted]` marker. It is categorically excluded from
the gated research-summary candidates: a Git revision listing is
not full code/history proof.

### Real executable to actual adapter end-to-end

The newly committed standalone Python gate
`scripts/experiments/rtk_oss_shadow_retention.py` received the **real
outputs** of the pinned upstream binary, not mocks:

- Cargo test filter: gate exit **3**; marker **0/1** retained;
  `research_summary_candidate=false`.
- Pytest filter: gate exit **3**; marker **0/1** retained;
  `research_summary_candidate=false`.
- Log filter: gate exit **0**; marker **1/1** retained;
  `research_summary_candidate=true` for synthetic,
  **non-authoritative research summary use only**.

This proves that the retention gate detects this family of marker
losses, not that every kind of failure is covered.

Every output sets `source_output_remains_authoritative=true`,
`compression_approved_for_claim_evidence=false`,
`automatic_harness_rewrite_allowed=false`,
`production_dispatch_allowed=false`, `production_gate=HOLD`,
and `actual_prompt_tokens_measured=false`.
It returns only numeric counts, checksums and status flags; it does
not print any raw content or marker string.

## Standalone gate contract

No RTK binary is required for the standalone gate itself.
It reads two existing absolute, regular, bounded
files: the **original** output and the compressed output.
Input files must be distinct and not symlinks; it rejects
missing required literals, unexpected filter names, malformed status
codes, excessive evidence size and malformed marker requirements.
The source and compacted SHA-256 digests identify the exact tested bytes,
but neither hash proves complete semantic fidelity nor authoritative
custody.

Reproduce using **synthetic-only** data:

```bash
python3 scripts/experiments/rtk_oss_shadow_retention.py \
  --source /tmp/synthetic-source.txt \
  --compacted /tmp/synthetic-rtk.txt \
  --filter cargo-test \
  --source-exit-status 1 \
  --required-marker FATAL_TRACE_HASH=8a593fac2bca17c4
```

Return codes: `0` = limited research candidate (still HOLD);
`3` = source-to-compacted retention/size gate failed;
`2` = refused malformed or incomplete evidence.
**Do not wire exit code 0 into production admission**.

The focused synthetic/negative test suite
`tests/test_rtk_oss_shadow_retention.py` passed **21/21**
on x1-370 at exact code head
`139104d14065a693d874aaddd88becb09fed30df`.
It covers marker loss, severity loss, git-log truncation, absent source,
oversize, symlink, duplicate markers, invalid status, invalid filter,
no-token-claim and no-dispatch. Python AST parsing passed.

## Decision and smallest next gates

- **Do not** enable RTK rewrite hooks on Hermes, PI, AssistX, CI,
  any test-failure channel or SGNC signed-witness/trace-custody path.
- A log-only compressed **secondary summary** may be evaluated when
  explicit required markers survive, but the full original remains
  retrievable under a separate signed, versioned retention system.
  This adapter by itself is not that system.
- Before adoption for successful routine tool output, run paired
  end-to-end *productive coding tasks*, verifying task completion,
  exact error diagnostics, source file locations, CI outcomes,
  measured **actual prompt tokens**, context relevance and
  secondary decompression/digest parity. Compare to uncompressed
  and Headroom under identical tasks.
- An isolated Headroom evaluation must disable default telemetry
  (`HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`,
  `HEADROOM_TELEMETRY=off`, `DO_NOT_TRACK=1`).
- Do not make a model-utilization claim from source-output byte
  reductions or from test-only receipts. Production **NO-GO**.
