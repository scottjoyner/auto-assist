#!/usr/bin/env python3
"""Offline, read-only tgrep shadow scan parity guard.

Only supports literal-file discovery. Index results are never authoritative:
ripgrep verifies actual filesystem membership and supplies the final count.
No source text, filenames, arguments, or secret-bearing paths are exported.
No daemons, index creation, model routing, admission, or remote network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

MAX_MATCH_BYTES = 2_000_000
MAX_BIN_BYTES = 100_000_000
PIN = re.compile(r"[a-fA-F0-9]{64}\Z")


class ProbeRejected(ValueError):
    pass


def verify_executable(binary: Path, sha256: str) -> None:
    if type(sha256) is not str or PIN.fullmatch(sha256) is None:
        raise ProbeRejected("invalid expected digest")
    if not binary.is_absolute() or not binary.is_file() or binary.is_symlink():
        raise ProbeRejected("binary must be a regular absolute file")
    if binary.stat().st_size > MAX_BIN_BYTES:
        raise ProbeRejected("binary too large")
    if hashlib.file_digest(binary.open("rb"), "sha256").hexdigest() != sha256.lower():
        raise ProbeRejected("binary hash mismatch")


def parse_paths(output: bytes, root: Path) -> set[str]:
    if len(output) > MAX_MATCH_BYTES:
        raise ProbeRejected("result exceeded fixed bound")
    results = set()
    # Upstream -l --null emits literal paths terminated with NUL, even if the
    # filename contains newlines. Resolve and bound path membership.
    if output and not output.endswith(b"\0"):
        raise ProbeRejected("unterminated filename result")
    for b in output.split(b"\0"):
        if not b:
            continue
        try:
            raw = os.fsdecode(b)
            p = Path(raw)
            rel = p.resolve(strict=False).relative_to(root)
            if not rel.parts or p.is_symlink():
                raise ProbeRejected("symlink or invalid result")
        except (ValueError, OSError, RuntimeError):
            raise ProbeRejected("path outside checkout") from None
        if rel.as_posix() in results:
            raise ProbeRejected("duplicate result")
        results.add(rel.as_posix())
    return results


def call_search(args: list[str], root: Path) -> tuple[set[str], float]:
    started = time.perf_counter()
    try:
        ret = subprocess.run(
            args, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=15,
            env={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
            cwd=str(root),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProbeRejected("probe failed") from None
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    if ret.returncode not in (0, 1):
        raise ProbeRejected("invalid probe result")
    return parse_paths(ret.stdout, root), elapsed


def compare(
    root: Path,
    index: Path,
    pattern: str,
    tgrep: Path,
    sha256: str,
    *,
    rg: str = "/usr/bin/rg",
) -> dict:
    if type(pattern) is not str or not (1 <= len(pattern) <= 96):
        raise ProbeRejected("bounded literal pattern required")
    if "\0" in pattern or "\n" in pattern or "\r" in pattern:
        raise ProbeRejected("multiline pattern unsupported")
    if not root.is_absolute() or not root.is_dir() or not (root / ".git").exists():
        raise ProbeRejected("source checkout must be an absolute Git directory")
    if not index.is_absolute() or not index.is_dir():
        raise ProbeRejected("existing isolated index required")
    if index == root or index.is_relative_to(root) or root.is_relative_to(index):
        raise ProbeRejected("index cannot overlap checkout")
    verify_executable(tgrep, sha256)
    if rg != "/usr/bin/rg" or not Path(rg).is_file():
        raise ProbeRejected("trusted local ripgrep missing")
    root = root.resolve()
    scan, scan_ms = call_search(
        [rg, "-l", "-0", "-F", "--max-filesize", "2M", "--", pattern, str(root)], root
    )
    index_set, index_ms = call_search(
        [str(tgrep), "--index-path", str(index), "--max-filesize", "2M",
         "-l", "-0", "-F", "--", pattern, str(root)], root
    )
    # Re-verify the scanner semantics against tgrep's own live-file mode.
    tscan, tscan_ms = call_search(
        [str(tgrep), "--no-index", "--max-filesize", "2M",
         "-l", "-0", "-F", "--", pattern, str(root)], root
    )
    parity = scan == index_set
    scan_agreement = scan == tscan
    return {
        "kind": "tgrep_shadow_scan_parity_v1",
        "source": "microsoft/tgrep",
        "index_authoritative": False,
        "source_data_exported": False,
        "file_paths_exported": False,
        "indexed_file_matches": len(index_set),
        "scanner_file_matches": len(scan),
        "tgrep_live_scan_file_matches": len(tscan),
        "index_matches_live_scan": parity and scan_agreement,
        "scan_engines_agree": scan_agreement,
        "stale_or_divergent_index": not parity,
        "verification_complete": scan_agreement,
        "rg_ms": scan_ms,
        "tgrep_index_ms": index_ms,
        "tgrep_live_scan_ms": tscan_ms,
        "dispatch_allowed": False,
        "harness_change_allowed": False,
        "production_gate": "HOLD",
        "note": "Current scan is the reference; indexed results are exploratory only. No runtime or claim was inspected.",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--binary-sha256", required=True)
    p.add_argument("--literal", required=True)
    a = p.parse_args(argv)
    try:
        out = compare(a.root, a.index, a.literal, a.binary, a.binary_sha256)
    except (ProbeRejected, ValueError, OSError):
        print("tgrep shadow: refused unverified source, index, or result", file=sys.stderr)
        return 2
    print(json.dumps(out, sort_keys=True, allow_nan=False))
    # Treat mismatched search engines as a failed evidence check.
    return 0 if out["scan_engines_agree"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
