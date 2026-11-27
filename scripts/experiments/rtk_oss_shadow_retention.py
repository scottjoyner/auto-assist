#!/usr/bin/env python3
"""Offline, fail-closed RTK OSS tool-output retention assessment.

Compare source-true output against a precomputed compacted copy. Never
execute commands, RTK, models, network, user hooks, or external services.
SHA-256 receipts cannot replace the original failure evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys

MAX_BYTES = 1_000_000
MAX_MARKERS = 16
FILTERS = {"cargo-test", "pytest", "git-log", "log"}
FAILURE_TERMS = re.compile(rb"(?i)(?:\bfail(?:ed|ure)?\b|\berror\b|\bfatal\b|\bpanic(?:ked)?\b|\btraceback\b)")
TRUNCATION_TERMS = re.compile(rb"(?i)(?:\[\+\d+ lines omitted\]|\btruncated\b|\bomitted\b)")


class EvidenceRejected(ValueError):
    """Incompatible or untrusted local evidence."""


def read_regular(path: Path) -> tuple[bytes, str]:
    if not path.is_absolute() or path.is_symlink():
        raise EvidenceRejected("absolute, non-symlink evidence files required")
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
        raise EvidenceRejected("oversize or non-regular evidence")
    # Strict at-read bound even if the source grows after stat() (best effort).
    with path.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise EvidenceRejected("evidence grew past limit")
    return data, hashlib.sha256(data).hexdigest()


def assess(
    raw: bytes,
    compact: bytes,
    *,
    mode: str,
    source_exit_status: int,
    markers: list[str],
) -> dict:
    if mode not in FILTERS or type(source_exit_status) is not int or not 0 <= source_exit_status <= 255:
        raise EvidenceRejected("unsupported filter or source exit code")
    if not raw or len(raw) > MAX_BYTES or len(compact) > MAX_BYTES:
        raise EvidenceRejected("invalid evidence size")
    if type(markers) is not list or not 1 <= len(markers) <= MAX_MARKERS:
        raise EvidenceRejected("explicit retention requirements are mandatory")
    if any(
        type(marker) is not str or not 4 <= len(marker.encode("utf-8")) <= 200
        or "\n" in marker or "\r" in marker or "\0" in marker
        for marker in markers
    ):
        raise EvidenceRejected("invalid literal retention marker")
    if len(set(markers)) != len(markers):
        raise EvidenceRejected("duplicate markers")
    marker_bytes = [m.encode("utf-8") for m in markers]
    if any(item not in raw for item in marker_bytes):
        raise EvidenceRejected("marker requirement not in source-true output")
    preserved = [item in compact for item in marker_bytes]
    source_failure_like = bool(FAILURE_TERMS.search(raw))
    compact_failure_like = bool(FAILURE_TERMS.search(compact))
    # Both failure text and numeric source-exit status are externally supplied;
    # a pipe filter's exit code does NOT provide the underlying command status.
    has_failure = source_exit_status != 0 or source_failure_like
    output_smaller = len(compact) < len(raw)
    all_preserved = all(preserved)
    research_candidate = (
        all_preserved and output_smaller and not (has_failure and not compact_failure_like)
        and mode in {"log", "cargo-test", "pytest"}
    )
    return {
        "kind": "rtk_oss_shadow_retention_v1",
        "rtk_upstream": "rtk-ai/rtk",
        "rtk_edition": "rtk-oss",
        "rtk_release": "v1.1.0",
        "filter": mode,
        "source_exit_status_supplied": source_exit_status,
        "source_failure_like": source_failure_like,
        "compacted_failure_like": compact_failure_like,
        "original_bytes": len(raw),
        "compacted_bytes": len(compact),
        "byte_reduction_pct": round(100 * (1 - len(compact)/len(raw)), 2),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "compacted_sha256": hashlib.sha256(compact).hexdigest(),
        "required_markers": len(marker_bytes),
        "preserved_markers": sum(preserved),
        "missing_required_marker": not all_preserved,
        "truncation_signal_in_compacted_output": bool(TRUNCATION_TERMS.search(compact)),
        "research_summary_candidate": research_candidate,
        "source_output_remains_authoritative": True,
        "actual_prompt_tokens_measured": False,
        "model_output_tokens_generated": 0,
        "signed_witness_verified": False,
        "compression_approved_for_claim_evidence": False,
        "automatic_harness_rewrite_allowed": False,
        "production_dispatch_allowed": False,
        "production_gate": "HOLD",
        "note": "Synthetic evidence retention only; hashes and markers do not prove complete semantic fidelity.",
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--compacted", type=Path, required=True)
    p.add_argument("--filter", choices=sorted(FILTERS), required=True)
    p.add_argument("--source-exit-status", type=int, required=True)
    p.add_argument("--required-marker", action="append", required=True)
    args = p.parse_args(argv)
    try:
        if args.source == args.compacted:
            raise EvidenceRejected("source and result must be distinct")
        source, _ = read_regular(args.source)
        compact, _ = read_regular(args.compacted)
        if args.source.samefile(args.compacted):
            raise EvidenceRejected("source and result share file identity")
        receipt = assess(source, compact, mode=args.filter,
                         source_exit_status=args.source_exit_status,
                         markers=args.required_marker)
    except (OSError, ValueError, UnicodeError):
        print("RTK shadow assessment refused: invalid or incomplete evidence", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, allow_nan=False))
    return 0 if receipt["research_summary_candidate"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
