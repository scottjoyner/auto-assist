#!/usr/bin/env python3
"""Read-only, fail-closed model-fit *advice* from llmfit API JSON.

Source: llmfit /api/v1/models or /api/v1/models/top envelope (not a live
AssistX runtime projection).  This module has no network, subprocess, database,
model-loading, admission, lease, or routing capabilities.

Never use output as a physical runtime, loading, or execution attestation.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

MAX_INPUT_BYTES = 2_000_000
MAX_MODELS = 500
FIT_LEVELS = {"perfect", "good", "marginal", "too_tight"}
DEFAULT_DISK_RESERVE_GB = 10.0


def _text(value: Any, key: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{key}: expected nonempty string (max {limit})")
    return value.strip()


def _number(value: Any, key: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key}: expected numeric value")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{key}: expected finite nonnegative value")
    return number


def make_advisory(
    snapshot: Mapping[str, Any],
    *,
    expected_node: str,
    upstream_commit: str,
    free_disk_gb: float | None = None,
    disk_reserve_gb: float = DEFAULT_DISK_RESERVE_GB,
) -> dict[str, Any]:
    """Produce a bounded, deterministic, non-authoritative model-fit report.

    Only accepts a single *named* llmfit node; network-derived inventory is
    not fetched.  Recommendations are not used to select runtime/model aliases.
    """
    node = _text(expected_node, "expected_node")
    commit = _text(upstream_commit, "upstream_commit", 128)
    disk = _number(free_disk_gb, "free_disk_gb")
    reserve = _number(disk_reserve_gb, "disk_reserve_gb")
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot: expected JSON object")
    source_node = snapshot.get("node")
    if not isinstance(source_node, Mapping):
        raise ValueError("snapshot.node: llmfit API node object required")
    reported_node = _text(source_node.get("name"), "snapshot.node.name")
    if reported_node.casefold() != node.casefold():
        raise ValueError("snapshot node does not match expected node")
    if not isinstance(snapshot.get("system"), Mapping):
        raise ValueError("snapshot.system: hardware object required")
    entries = snapshot.get("models")
    if not isinstance(entries, list) or len(entries) > MAX_MODELS:
        raise ValueError(f"snapshot.models: expected array of <= {MAX_MODELS}")
    if isinstance(snapshot.get("returned_models"), int) and snapshot["returned_models"] != len(entries):
        raise ValueError("returned_models disagrees with model array length")
    seen: set[tuple[str, str, str]] = set()
    out = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(f"models[{i}]: expected object")
        name = _text(entry.get("name"), f"models[{i}].name")
        quant = _text(entry.get("best_quant"), f"models[{i}].best_quant", 96)
        runtime = _text(entry.get("runtime"), f"models[{i}].runtime", 96)
        level = _text(entry.get("fit_level"), f"models[{i}].fit_level", 32).lower()
        if level not in FIT_LEVELS:
            raise ValueError(f"models[{i}]: unknown fit level {level!r}")
        key = (name.casefold(), quant.casefold(), runtime.casefold())
        if key in seen:
            raise ValueError(f"models[{i}]: duplicate model/quant/runtime")
        seen.add(key)
        score = _number(entry.get("score"), f"models[{i}].score")
        footprint = _number(entry.get("disk_size_gb"), f"models[{i}].disk_size_gb")
        estimated_tps = _number(entry.get("estimated_tps"), f"models[{i}].estimated_tps")
        measured_tps = _number(entry.get("measured_tps"), f"models[{i}].measured_tps")
        if score is None or footprint is None:
            raise ValueError(f"models[{i}]: score and disk_size_gb required")
        installed = entry.get("installed")
        if not isinstance(installed, bool):
            raise ValueError(f"models[{i}].installed: expected boolean")
        disk_fit = "not_checked"
        if disk is not None:
            disk_fit = "estimated_weight_and_reserve_fit" if footprint + reserve <= disk else "insufficient_for_estimated_weights_and_reserve"
        out.append({
            "model": name,
            "quantization": quant,
            "runtime_hint": runtime,
            "fit_estimate": level,
            "fit_score": score,
            "estimated_weight_disk_gb": footprint,
            "estimated_tps": estimated_tps,
            "measured_tps_in_llmfit": measured_tps,
            "llmfit_installed_flag": installed,
            "disk_guard": disk_fit,
            "physical_load_state": "UNVERIFIED",
            "signed_runtime_id": None,
            "admitted": False,
            "dispatch_allowed": False,
        })
    out.sort(key=lambda row: (-row["fit_score"], row["model"].casefold(), row["quantization"]))
    return {
        "kind": "llmfit_shadow_model_fit_v1",
        "node": node,
        "source": {"project": "AlexsJones/llmfit", "commit": commit, "interface": "/api/v1/models"},
        "upstream_node_name_only": True,
        "physical_load_verified": False,
        "approval_or_reservation_verified": False,
        "authoritative_capacity": False,
        "dispatch_allowed": False,
        "observed_free_disk_gb_unattested": disk,
        "disk_reserve_gb": reserve,
        "models": out,
        "caveat": "Fit, installed flag, and measured-tps claims from llmfit never prove an AssistX signed physical runtime, claim, or available dispatch slot.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path, help="saved llmfit API JSON; no URLs")
    parser.add_argument("--node", required=True, help="expected llmfit node name")
    parser.add_argument("--upstream-commit", required=True, help="pinned upstream llmfit SHA")
    parser.add_argument("--free-disk-gb", type=float, default=None)
    parser.add_argument("--disk-reserve-gb", type=float, default=DEFAULT_DISK_RESERVE_GB)
    args = parser.parse_args(argv)
    try:
        if not args.snapshot.is_file() or args.snapshot.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("snapshot must be a regular file <= 2 MB")
        with args.snapshot.open("r", encoding="utf-8") as handle:
            raw = json.load(handle, parse_constant=lambda s: (_ for _ in ()).throw(ValueError("nonfinite JSON constant")))
        report = make_advisory(
            raw, expected_node=args.node, upstream_commit=args.upstream_commit,
            free_disk_gb=args.free_disk_gb, disk_reserve_gb=args.disk_reserve_gb,
        )
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(2, f"shadow advisory rejected input: {error}\n")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
