#!/usr/bin/env python3
"""Recommend a fleet loadout for the current circumstance.

The probe only reads state: LM Studio's loaded models, per-device VRAM from
sysfs, and which exclusive ports are already held. It never loads, unloads,
or restarts anything.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from assistx.fleet_loadout import (
    Policy,
    load_circumstance,
    recommend,
)


def _sysfs_vram() -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for card in sorted(Path("/sys/class/drm").glob("card*")):
        mem = card / "device"
        total = mem / "mem_info_vram_total"
        used = mem / "mem_info_vram_used"
        if not total.exists():
            continue
        try:
            total_bytes = int(total.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        used_bytes = 0
        if used.exists():
            try:
                used_bytes = int(used.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                used_bytes = 0
        devices.append(
            {
                "name": card.name,
                "vram_total_bytes": total_bytes,
                "vram_used_bytes": used_bytes,
            }
        )
    return devices


def _lms_loaded(lms: str) -> list[dict[str, Any]]:
    import subprocess

    try:
        completed = subprocess.run(
            [lms, "ps", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    entries = parsed if isinstance(parsed, list) else parsed.get("models", [])
    loaded: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_key = entry.get("identifier") or entry.get("path") or entry.get("modelKey")
        if not model_key:
            continue
        loaded.append(
            {
                "model_key": str(model_key),
                "device": str(entry.get("device") or ""),
                "vram_bytes": int(entry.get("vramBytes") or 0),
                "port": int(entry["port"]) if entry.get("port") else None,
            }
        )
    return loaded


def _listening_ports() -> set[int]:
    import socket

    ports: set[int] = set()
    for port in (8125, 1234):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                ports.add(port)
    return ports


def observe(*, lms: str = "lms") -> dict[str, Any]:
    return {
        "devices": _sysfs_vram(),
        "loaded_models": _lms_loaded(lms),
        "listening_ports": sorted(_listening_ports()),
    }


def render_commands(recommendation: dict[str, Any], policy: Policy) -> str:
    """Render the operator commands that would realize the recommendation.

    The recommender never applies anything. These commands are the operator's
    to read and run; the ``echo``/``review`` framing keeps that boundary
    explicit.
    """
    loadout_id = recommendation.get("recommended_loadout_id")
    lines = [
        "# Operator commands for the recommended fleet loadout.",
        f"# policy: {recommendation['policy_id']}",
        f"# circumstance: {recommendation['circumstance_id']}",
        f"# recommended: {loadout_id}",
        f"# authority: {json.dumps(recommendation['authority'], sort_keys=True)}",
        "# This file is advisory. Review before running anything.",
    ]
    if not loadout_id:
        lines.append("# No feasible loadout for this circumstance; nothing to apply.")
        return "\n".join(lines) + "\n"
    loadout = next(
        item for item in policy.loadouts if item["loadout_id"] == loadout_id
    )
    lines.append(f"# {loadout['description'] or loadout_id}")
    for resident in loadout["residents"]:
        if resident["kind"] == "runtime":
            lines.append(
                f"# runtime resident {resident['model_key']}: bind via the soak "
                "harness, not LM Studio:"
            )
            lines.append(
                f"#   bash $SOAK_DIR/bind-soak-runtime.sh <PREFIX_FOR_{resident['port']}>"
            )
        else:
            lines.append(f"lms load {resident['model_key']}")
    if not any(r["kind"] != "runtime" for r in loadout["residents"]):
        lines.append("lms unload --all   # nothing to hold resident")
    lines.append(
        "# Verify: curl -s $ENDPOINT/v1/models | jq '.data[].id'  (prod: :1234)"
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Recommend a fleet loadout for the current circumstance. "
            "Read-only probe; the recommendation carries all-false authority."
        )
    )
    parser.add_argument("--policy", required=True)
    parser.add_argument("--circumstance", required=True)
    parser.add_argument("--lms", default="lms")
    parser.add_argument("--output")
    parser.add_argument("--commands-out")
    args = parser.parse_args()

    policy = Policy.load(args.policy)
    circumstance_raw = json.loads(
        Path(args.circumstance).read_text(encoding="utf-8")
    )
    circumstance = load_circumstance(circumstance_raw)
    observation = observe(lms=args.lms)
    recommendation = recommend(
        policy,
        circumstance=circumstance,
        circumstance_id=str(circumstance_raw.get("circumstance_id", "unspecified")),
        vram_headroom_bytes=0,
        held_exclusive_ports=observation["listening_ports"],
    )
    recommendation["observed"] = observation
    text = json.dumps(recommendation, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    if args.commands_out:
        commands = Path(args.commands_out)
        commands.parent.mkdir(parents=True, exist_ok=True)
        commands.write_text(render_commands(recommendation, policy), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
