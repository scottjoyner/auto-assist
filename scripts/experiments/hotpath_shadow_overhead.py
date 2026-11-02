#!/usr/bin/env python3
"""Opt-in isolated hotpath 0.30.3 synthetic feature-overhead experiment.

No application repository is imported or modified; no live inference, agent
integration, hooks, daemon, production process, remote profiler or telemetry.
Downloads from crates.io are disabled unless explicitly authorized. A generated
Cargo.lock is checked and its digest recorded; it is not a cross-run lock pin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tomllib

VERSION = "0.30.3"
MANIFEST = """[package]
name = "fleet_hotpath_synthetic_pilot"
version = "0.1.0"
edition = "2021"
[dependencies]
hotpath = { version = "=0.30.3", optional = true, default-features = false }
[features]
profile = ["dep:hotpath", "hotpath/hotpath"]
"""
SOURCE = """use std::hint::black_box;
#[cfg_attr(feature = "profile", hotpath::measure)]
fn synthetic_work(mut value: u64, steps: u64) -> u64 {
    for _ in 0..steps {
        value = value.wrapping_mul(6_364_136_223_846_793_005)
                     .wrapping_add(1_442_695_040_888_963_407);
        value ^= value >> 27;
        black_box(value);
    }
    value
}
#[cfg_attr(feature = "profile", hotpath::main(format = "table", report = "functions-timing"))]
fn main() {
    let loops = 14_000_u64;
    let mut x = 0xdecafbad_u64;
    for i in 0..loops {
        x = synthetic_work(x ^ i, 160);
    }
    println!("checksum={x:016x} iterations={loops}");
}
"""
CHECKSUM = re.compile(r"checksum=[0-9a-f]{16} iterations=14000\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class Inconclusive(Exception):
    pass


def verify_lock(text: str) -> str:
    parsed = tomllib.loads(text)
    packages = parsed.get("package")
    if type(packages) is not list or not packages:
        raise Inconclusive("no dependency lock")
    names = [(p.get("name"), p.get("version")) for p in packages]
    if names.count(("hotpath", VERSION)) != 1:
        raise Inconclusive("unexpected hotpath version")
    for item in packages:
        src = item.get("source")
        if src is None:
            if item.get("name") != "fleet_hotpath_synthetic_pilot":
                raise Inconclusive("unapproved path or git dependency")
            continue
        if src != "registry+https://github.com/rust-lang/crates.io-index":
            raise Inconclusive("non-registry dependency")
        digest = item.get("checksum")
        if type(digest) is not str or SHA256.fullmatch(digest) is None:
            raise Inconclusive("missing registry digest")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_observation(stdout: bytes, stderr: bytes, enabled: bool) -> tuple[str, bool]:
    if len(stdout) > 100_000 or len(stderr) > 100_000:
        raise Inconclusive("unexpected excessive output")
    decoded = stdout.decode("utf-8", errors="strict")
    matches = [s.strip() for s in decoded.splitlines() if s.startswith("checksum=")]
    if len(matches) != 1 or CHECKSUM.fullmatch(matches[0]) is None:
        raise Inconclusive("missing deterministic checksum")
    # The profiler prints to stdout by default, not necessarily stderr.
    report = "synthetic_work" in decoded and ("timing" in decoded or "Timing" in decoded)
    if enabled and not report:
        raise Inconclusive("profiler did not produce validated function report")
    if not enabled and report:
        raise Inconclusive("baseline unexpectedly profiled")
    return matches[0], report


def stats(values: list[float]) -> dict:
    if not 3 <= len(values) <= 10 or any(not 0 < x < 100_000 for x in values):
        raise Inconclusive("timing vector outside limits")
    return {
        "samples": len(values),
        "median_ms": round(statistics.median(values), 4),
        "min_ms": round(min(values), 4),
        "max_ms": round(max(values), 4),
    }


def run(cargo: Path, samples: int, allow_downloads: bool) -> dict:
    if not allow_downloads:
        raise Inconclusive("explicit dependency-download authorization required")
    if not 3 <= samples <= 10:
        raise Inconclusive("samples outside bound")
    if not cargo.is_absolute() or not cargo.is_file() or not os.access(cargo, os.X_OK):
        raise Inconclusive("missing trusted absolute cargo executable")
    with tempfile.TemporaryDirectory(prefix="hotpath-shadow-", dir="/tmp") as w:
        root = Path(w)
        (root / "src").mkdir()
        (root / "cargo-home").mkdir()
        (root / "Cargo.toml").write_text(MANIFEST, encoding="utf-8")
        (root / "src" / "main.rs").write_text(SOURCE, encoding="utf-8")
        rustup_home = cargo.parents[2] / ".rustup"  # sibling of ~/.cargo, not under ~/.cargo
        env = {
            "PATH": f"{cargo.parent}:/usr/bin:/bin",
            "HOME": str(root),
            "CARGO_HOME": str(root / "cargo-home"),
            "CARGO_TARGET_DIR": str(root / "target"),
            "CARGO_BUILD_JOBS": "2",
            "CARGO_HTTP_TIMEOUT": "20",
            "CARGO_NET_RETRY": "1",
            "CARGO_REGISTRIES_CRATES_IO_PROTOCOL": "sparse",
        }
        if rustup_home.is_dir():
            env["RUSTUP_HOME"] = str(rustup_home)
        def invoke(cmd: list[str], seconds: int, *, offline: bool = False):
            args = [str(cargo), *cmd]
            if offline and "build" in cmd:
                args.insert(2, "--offline")
            try:
                p = subprocess.run(
                    args, cwd=root, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=seconds, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise Inconclusive("bounded compilation failed") from None
            if p.returncode:
                raise Inconclusive("cargo resolution or build rejected")
        invoke(["generate-lockfile", "--quiet"], 75)
        lock_sha = verify_lock((root / "Cargo.lock").read_text("utf-8"))
        invoke(["build", "--locked", "--release", "--features", "profile", "--quiet"], 150)
        name = "fleet_hotpath_synthetic_pilot"
        binary = root / "target" / "release" / name
        profiled = root / "profiled"
        shutil.copy2(binary, profiled)
        invoke(["build", "--locked", "--release", "--quiet"], 100, offline=True)
        plain = root / "plain"
        shutil.copy2(binary, plain)
        durations = {"plain": [], "profiled": []}
        checksums = set()
        have_report = False
        runenv = {"PATH": "/usr/bin:/bin", "HOME": str(root)}
        for _ in range(samples):
            for label, exe in (("plain", plain), ("profiled", profiled)):
                start = time.perf_counter()
                try:
                    p = subprocess.run(
                        [str(exe)], cwd=root, env=runenv,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=15, check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    raise Inconclusive("synthetic run failed") from None
                latency = (time.perf_counter() - start) * 1000
                if p.returncode:
                    raise Inconclusive("synthetic run returned nonzero")
                try:
                    checksum, report = parse_observation(p.stdout, p.stderr, label == "profiled")
                except UnicodeError:
                    raise Inconclusive("non-text result") from None
                checksums.add(checksum)
                have_report |= report
                durations[label].append(latency)
        if len(checksums) != 1 or not have_report:
            raise Inconclusive("missing checksum parity or profiler output")
        plain_stats = stats(durations["plain"])
        prof_stats = stats(durations["profiled"])
        return {
            "kind": "hotpath_opt_in_synthetic_overhead_v1",
            "upstream_repo": "hotpath-rs/hotpath",
            "upstream_version": VERSION,
            "cargo_lock_sha256": lock_sha,
            "source_sha256": hashlib.sha256(SOURCE.encode("utf-8")).hexdigest(),
            "checksum": next(iter(checksums)),
            "profile_report_present": have_report,
            "plain": plain_stats,
            "profiled": prof_stats,
            "profiled_over_plain_median_ratio": round(
                prof_stats["median_ms"] / plain_stats["median_ms"], 4
            ),
            "resource_measurement_scope": "local subprocess total elapsed, interleaved fixed workload",
            "runtime_network_isolation_verified": False,
            "actual_fleet_service_profiled": False,
            "actual_model_tokens_generated": 0,
            "instrumentation_policy": "off_by_default",
            "production_gate": "HOLD",
            "deployment_authorized": False,
            "cloud_export_enabled": False,
            "inference_dispatch_allowed": False,
            "note": "Synthetic microbenchmark with call-level instrumentation; overhead is not generalizable.",
        }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cargo", type=Path, required=True)
    p.add_argument("--samples", type=int, default=6)
    p.add_argument("--allow-dependency-downloads", action="store_true")
    args = p.parse_args(argv)
    try:
        result = run(args.cargo, args.samples, args.allow_dependency_downloads)
    except (Inconclusive, OSError, UnicodeError, TypeError):
        print("hotpath research: refused or experiment incomplete", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
