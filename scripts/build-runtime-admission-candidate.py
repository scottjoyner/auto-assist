#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import stat
import sys
from typing import Any

import yaml

from assistx.runtime_admission import (
    RuntimeAdmissionContractError,
    build_runtime_admission_candidate,
    validate_runtime_admission_candidate,
)


def _safe_input(path_text: str, label: str) -> pathlib.Path:
    path = pathlib.Path(path_text).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} must be a regular nonsymlinked file")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o022:
        raise ValueError(f"{label} must not be group/world writable")
    return path


def _load(path: pathlib.Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        value = yaml.safe_load(text)
    else:
        value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _write(path_text: str, payload: dict[str, Any]) -> pathlib.Path:
    path = pathlib.Path(path_text).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-self-authorizing AssistX runtime admission candidate "
            "from a verified disabled fleet profile and current live proof."
        )
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--live-proof", required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--expected-current-generation", type=int, required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--ttl-seconds", type=int, default=300)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--allow-single-path",
        action="store_true",
        help="Do not require both LAN and Tailscale proof.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        profile_path = _safe_input(args.profile, "profile")
        live_path = _safe_input(args.live_proof, "live proof")
        candidate = build_runtime_admission_candidate(
            _load(profile_path),
            _load(live_path),
            generation=args.generation,
            expected_current_generation=args.expected_current_generation,
            approved_by=args.approved_by,
            approval_id=args.approval_id,
            ttl_seconds=args.ttl_seconds,
            require_lan_and_tailscale=not args.allow_single_path,
        )
        validate_runtime_admission_candidate(candidate)
        output = _write(args.out, candidate)
    except (OSError, ValueError, RuntimeAdmissionContractError) as exc:
        print(f"runtime admission candidate rejected: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "candidate_id": candidate["candidate_id"],
                "candidate_fingerprint": candidate["candidate_fingerprint"],
                "lease_id": candidate["lease"]["lease_id"],
                "lease_expires_at_ms": candidate["lease"]["expires_at_ms"],
                "generation": candidate["lease"]["generation"],
                "output": str(output),
                "admission_applied": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
