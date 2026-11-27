# Starred hotpath 0.30.3 — opt-in profiler overhead evidence

**Date:** November 2, 2026 · node: x1-370 · **research-only, production HOLD**.

## Candidate and why this matters

[hotpath-rs/hotpath](https://github.com/hotpath-rs/hotpath), MIT, upstream
release **v0.30.3** (October 31, 2026) provides feature-gated Rust
CPU/time/allocation/async profiling. It might help diagnose slow
SensorGraft graph retrieval, Rust vector services, and AssistX/Hermes
workflows. It is not a scheduler, new graph authority, model backend or
proof of fleet-wide inference token utilization.

The high-frequency-call overhead must be measured before making profiling
always-on. The upstream documented agent setup and skill installers were
**not run**.

## What was actually executed

An independently authored **synthetic Rust binary** with a stable checksum:
14,000 calls of a small `synthetic_work` function (160 arithmetic steps
each), `std::hint::black_box` to prevent trivial optimization.
Built both an ordinary Rust **release** binary and a version with
`#[hotpath::measure]` on the hot function and `#[hotpath::main(...)]`.
The sample uses `#[cfg_attr(feature="profile", ...)]` so profiler macros
are absent from ordinary builds.

The direct hotpath dependency is pinned to **`=0.30.3`**, with
`default-features = false`. Only the explicit `hotpath/hotpath`
instrumentation feature is enabled for the profiled build. Cloud,
network MCP, telemetry export, prometheus, CPU sampling and allocation
profiling **were not enabled**.

Both builds used Rustup/Cargo from x1-370 with an *isolated temporary*
`HOME`, `CARGO_HOME`, `CARGO_TARGET_DIR`, two Cargo build jobs, and
an explicit dependency download stage. The downloaded crates were
constrained to the crates.io registry and required lockfile checksums;
the generated transitive Cargo.lock SHA-256 was
`2bcdfe1182eaef12586ef60fa89bac957519ba7f95f81dbc6d51d8079ca92f93`.
That digest is an execution record **not a committed immutable transitive
lockfile**. Subsequent resolutions can vary unless the lock is separately
pinned. All experiment folders were deleted on exit; nothing was
installed into the application or persistent fleet services.

**Important risk boundary:** Cargo compilation ran as the current
user on the host, with isolated process env and temp directories;
the builder was *not* otherwise containerized or network-namespace
isolated, and build scripts can execute code. This does **not** qualify
as a sandboxed dependency supply-chain attestation and cannot justify
running arbitrary new dependencies on production nodes. In future,
compile in an unprivileged sandbox with pinned transitive lock, artifact
checksums and offline runtime where available.

The binaries were invoked alternately **six times each** on x1-370;
the caller measured end-to-end subprocess wall time, including process
startup and report overhead, not just a function's instruction cycles.
Both variants returned identical
`checksum=103f8b718d0e63dc iterations=14000`.
The profiled variant emitted a validated function-level timing report.

## Two independent runs of the same workload

Initial direct test:

| Metric | Plain | hotpath instrumentation |
| --- | ---: | ---: |
| Samples | 6 | 6 |
| Median subprocess time | **4.616 ms** | **7.078 ms** |
| Ratio, profiled/plain | | **1.533×** |

Exact-committed experiment with stronger output/lockfile validation
at code commit `5394125dc76213328ba9d75e4cfa67e27301f1b9`:

| Metric | Plain | hotpath instrumentation |
| --- | ---: | ---: |
| Samples | 6 | 6 |
| Median subprocess time | **4.8297 ms** | **7.3023 ms** |
| Min–max time | 4.7012–5.3774 ms | 7.0643–8.5485 ms |
| Ratio, profiled/plain | | **1.512×** |

**This is approximately 51% higher median process elapsed time on a
very short, heavily instrumented synthetic workload.** Because the
workload is only a few milliseconds, startup/report overhead and
one profiler event per call have outsized effects. It would be invalid
to extrapolate 51% to production SensorGraft, LFM3 or other fleet work,
or to claim the report was generated with no overhead.

The measurement produced **zero inference tokens**. It did not probe
live services, measure actual model bottlenecks, mutate the graph or
compare node-wide utilization.

## Research adapter

`scripts/experiments/hotpath_shadow_overhead.py` is a Python
standard-library experiment; **it does nothing without the explicit**
`--allow-dependency-downloads` option, and never invokes user-supplied
scripts. Its only Rust input is the fixed checked-in synthetic program.
It uses a disposable source/project/Cargo home, verifies the explicit
hotpath version and registry-only checksum semantics, compiles release
profile and baseline, verifies identical deterministic checksums,
requires a real profile report, reports bounded measurements and retains
no binaries. No default agent hooks, persistence, cloud export or
production permissions.

For manually authorized research-only use on x1-370:

```bash
python3 scripts/experiments/hotpath_shadow_overhead.py \
  --cargo /home/scott/.cargo/bin/cargo \
  --samples 6 --allow-dependency-downloads
```

Without `--allow-dependency-downloads`, the command fails closed before
executing Cargo. The script does not enforce network namespace isolation,
nor provide a true zero-overhead instrumentation guarantee.

Focused unit tests (without Rust dependency installation):

```bash
python3 -m unittest discover -s tests -p test_hotpath_shadow_overhead.py -v
```

**21/21 synthetic/adversarial unit tests passed** on x1-370 at
commit `5f6a20625914a55b61b959785334275af0fbca1c`, along with
Python AST parse. The exact negative test suite includes registry and
checksum validation, explicit download authorization, profiled/baseline
report contracts, stable result requirements, and invalid timing bounds.

## Decision and future gates

Use hotpath as an **explicit short-window profiler on isolated,
nonproduction Rust reproductions**, not as an always-on daemon.
Prefer first identifying a real reproducible bottleneck using
production-safe read-only aggregated metrics and only then deciding
which specific function deserves temporary instrumentation.

Next independent validations: pinned transitive Cargo.lock, sandboxed
dependency build; equal release build flags/cpuset on a standalone
SensorGraft Rust benchmark, with feature OFF/ON on real identical
synthetic queries; report-value verification, memory/threads,
tail latency, compilation overhead, repeated independent runs and
concurrency. Avoid cloud/report export, privileged profiler hooks or
live trace inspection. Keep separate AssistX admission/SGNC custody
fences. **Production NO-GO; draft only.**
