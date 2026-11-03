# Starred GitHub audit — bounded Ripwire code-context shadow

**November 3, 2026 (America/New_York)** · AssistX research draft ·
**no production hooks, MCP server, source export, live traces or inference**

## Decision

A refreshed full traversal of the public `scottjoyner`
GitHub stars returned **1,068** repositories (pages 1–10: 100
each, page 11: 68, page 12: empty). Among newer candidate
capabilities:

- **[microsoft/tgrep](https://github.com/microsoft/tgrep)**,
  MIT, v1.2.6 / source SHA `df30218b0e42bd6c0faaf294d1fb0c16b9e7dd4b`:
  trigram-indexed code grep. Its binary release archive
  SHA-256 `582737741d93c1281802188da6b7207d4a5f16b1dbe010badff12f03414d99c4`
  exactly matched GitHub release metadata and checksums.
- **[redhat-et/ripwire](https://github.com/redhat-et/ripwire)**,
  Apache-2.0, v0.6.9, project/source reviewed at
  `dfd26fc1ea24a04bfe5cb4e5397d3fc9550fb4c4`:
  one local C++ code graph/context navigator. It emits
  ranked definitions and explicitly labels confidence
  rather than requiring a separate graph database.
- **[ovg-project/kvcached](https://github.com/ovg-project/kvcached)**,
  Apache-2.0, source SHA
  `415d8273f3bd66007c176d5da56aa13d9ccaae57`:
  v0.1.6 describes AMD/ROCm KV-cache layouts and support
  for vLLM up to 0.30.0 and SGLang up to 0.5.20
  on modern Torch versions. It **does not accelerate**
  existing llama.cpp servers automatically.
- **[cheaper-inference/OmniRoute](https://github.com/cheaper-inference/OmniRoute)**,
  MIT, a free-tier aggregator. Its roughly **1.62B
  tokens/month** headline is a *theoretical aggregate of
  separately owned and eligible third-party accounts*,
  not tokens provided to the fleet. Provider ToS,
  region, eligibility, pooled quota and actual enabled
  credentials would need independent verification before
  any free-first Auto-Router provider could be admitted.

## Actual local hardware-fit check

Using existing read-only SSH from `x1-370`,
`joyner` and `xwing` ordinary Python environments
did **not** have `vllm`, `sglang`, or `kvcached`
installed. Joyner has an existing `llama-server`
process, while xwing has an active `llama-server`
and additional Hermes/inference workloads. Thus
**do not replace or modify production runtime
stacks to try kvcached without isolated baseline,
AMD ABI proof and owner-approved cutover**.

OmniRoute's own catalog distinguishes recurring
free quotas, promotional credits, region/identity
gates, and `tos: avoid` entries. Treat it as
*untrusted candidate discovery only*, **not**
a source of production rate limits or
free-provider authorization. No external provider
credentials or model requests were used here.

## tgrep vs existing ripgrep: measured selection

Built a **read-only source-only snapshot** of
309 files (2,350,618 bytes) from the existing
SensorGraft `src/research/tests/contracts` code
subtrees. Only extensions `.py/.rs/.toml/.md/.sh`
were copied; no JSONL, raw live traces, media or
symlinks. The snapshot and tgrep index were
outside the source checkout, in disposable
`/tmp` trees. Both search tools ran in
network-disabled, nonroot read-only containers.

The trigram index contained 309 text files,
38,364 trigrams and occupied approx **4.1 MiB**.
All **eight** tested fixed-string search patterns
agreed exactly with ripgrep's matching-file sets
(hash equality as well as file count), but timing
showed no broad win on this small repository:

| Fixed-string query | tgrep p50 ms | ripgrep p50 ms |
| --- | ---: | ---: |
| `receipt` | 5.484 | 3.193 |
| `SGNC` (absent) | 2.427 | 3.081 |
| `def ` | 6.138 | 3.296 |
| `class ` | 3.686 | 3.370 |
| `policy` | 3.187 | 3.045 |
| `Graph` | 3.055 | 3.049 |
| `function` | 2.763 | 3.008 |
| unlikely absent pattern | 2.179 | 2.798 |

Nine timed calls per query per tool, alternating
invocation order, including fresh process
startup. **No tgrep daemon was started.**
Do not replace ripgrep or install a persistent
index into the fleet on this evidence. A much
larger codebase would need a separate test,
including index-refresh correctness, CPU and
memory headroom.

## Ripwire: actual source-navigation trials

Downloaded **official GitHub Ripwire v0.6.9 Linux
x86-64** release tarball and its checksum file.
The archive SHA-256 matched the GitHub release
asset: `5444af4124bec689c36a65879b44389dd18fda9fd4ad2037369fc6d27625080a`.
Extracted executable SHA-256:
`d72d639b298a01316fbd967af8156b8787c2cbe03ae6f0a91b10d8b9752abec2`
(55,782,872 bytes). These are **publisher-hosted
release digests**, not independent binary signatures
or a source reproducibility/build attestation.

Inside a network-disabled, nonroot, read-only
container, with one CPU, bounded memory and
ephemeral storage, ran the **actual** Ripwire
binary against the same source-only snapshot,
using:
`--no-cache --json --signatures-only --token-budget=2400
--max-memory=256M --for=<public task>`.

Five human-preselected relevant-file tests,
3 cold-parse runs each (no shared warm cache):

| Task | Known relevant file rank(s) | Median elapsed |
| --- | --- | ---: |
| Signed witness epoch rotation | `m61_external_epoch_gate.py` #1, `m62_native_epoch_precommit.py` #2, `m63_epoch_authority_lock.py` #6 | 2,116.5 ms |
| Resource-first ACK | `m55_resource_first_ack.py` #19 | 1,949.7 ms |
| Journal/recovery | `src/journal.rs` #6 | 2,050.2 ms |
| Checkpoint and highwater | `m50_verified_checkpoint.py` #10; highwater gold not returned | 2,004.5 ms |
| Runtime identity verification | `m58_runtime_custody.py` #7 | 1,944.5 ms |

Every task included **at least one** preselected
gold source file, but one gold source was missed,
other correct files may be unranked and
**four of five results reported `confidence=low`**.
The fifth reported `high`. Those labels are the
tool's own, **not independent verified confidence**.

The requested **2,400 token** budget is **soft**:
output `est_tokens` ranged 2,695–2,760,
above the request in all five cases.
These are tool-estimated tokens, **not measured
input tokens under an actual target tokenizer**.
This test did not run an LLM or measure full task
accuracy, repaired code, model answer
correctness, total context consumption or
tokens-per-useful-completion.

**Decision:** Ripwire is worth an explicitly
opt-in, bounded code-context *research adapter*;
it must not replace `rg` as a definitive
content search or make authoritative
security/release decisions.

## The added research-only code path

`research/ripwire_code_context_shadow.py`:

1. Defaults to **no source access** until
   `--allow-code-read` is supplied. Requires
   a canonical non-symlink repository root
   and the exact pinned Ripwire executable
   SHA-256, refusing executable oversize
   and symlinked paths.
2. Copies **at most 3,000 source-only files,
   25 MiB total, 200,000 bytes each**, from
   named `src/research/tests/contracts`
   subtrees into disposable private storage.
   Whitelisted extensions include code,
   TOML and Markdown; skips hidden paths,
   symlinks, caches and excluded media/trace
   formats. Source reads are bounded and
   checked for inode/mtime/size changes.
   It does not use or mutate real graph
   data, live traces, media or repositories.
3. Runs only a pinned release executable in
   a locally available Docker image with
   `--pull=never`, `--network=none`,
   `--read-only`, nonroot UID 65534,
   dropped Linux capabilities, one CPU,
   750 MiB memory, 70 PID limit, 128 MiB
   tmpfs and hard inner/outer timeouts.
   **No agent hooks, source write mounts,
   MCP server, external model calls or
   credential forwarding.**
4. Parses bounded JSON from Ripwire, rejects
   duplicate fields, nonfinite values, false
   confidence types, invalid token estimates,
   path escapes, nonexistent/symlinked
   referenced files and duplicate ranks.
   Emits at most **12 ranked, snapshot-grounded
   relative source filenames**, request
   hash, tool-estimated token budget/overrun,
   confidence and explicit uncertainty.
   **Source contents, signatures and raw
   diagnostics are not included in output.**
5. Every report records
   `source_custody_verified=false`,
   `independent_relevance_verified=false`,
   `model_quality_verified=false`,
   `model_tokens_used=0`,
   `network_egress_allowed=false`,
   `agent_hooks_installed=false`,
   and **`production_gate=HOLD`**.
   Confidence and file ranks are **not**
   authority grants.

The exact GitHub code/test HEAD
`9321b055bc6efa99752f278cdbea45d377ba9c51`
passed **42/42 unittest tests**, **42/42 pytest tests
(46 expanded subtests)**, Ruff check/format
and Python AST parsing. The tests
use mocked subprocess execution and synthetic
source files; no binary or model is downloaded
by unit tests.

The same code HEAD was independently used
for an actual complete offline invocation
against the 309-file SensorGraft source
snapshot. It emitted:
- 309 files / 2,350,618 bytes
  staged; 318 excluded entries;
- 12 candidate filenames, best-ranked
  `research/m61_external_epoch_gate.py`;
- `confidence=low`;
- `est_tokens=2698` versus
  requested `2400` (explicit overrun);
- **no exported body text, no network egress,
  `production_gate=HOLD`**.

## Smallest safe next gates

1. Benchmark against **fresh, predeclared
   hidden-answer coding tasks** on public
   repositories; report correct fix rates,
   missed evidence, rank precision/recall
   and tokens consumed by a real target
   model, not merely filename inclusion.
2. Test source snapshot **freshness and
   concurrent edit** rejection against
   synthetic repo updates (including
   fast file replacement).
3. Add a **trusted source classification
   contract** for signed provenance,
   secrets, customer data, and live
   SensorGraft traces. An extension
   whitelist is not an independent DLP
   or custody verifier.
4. Independently verify binary provenance
   and the Docker sandbox properties
   on every intended fleet node.
5. Only after review, decide whether
   a manually invoked agent helper
   meaningfully improves correctness,
   productivity and token budgets. No
   automatic hooks, MCP exposure or
   route changes without separate approval.

**This project has not increased production
inference utilization.** A faster navigation
map is potentially useful only if it improves
downstream task completion, which is not yet
proved by this experiment.
