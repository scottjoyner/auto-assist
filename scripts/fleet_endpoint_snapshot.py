#!/usr/bin/env python3
"""Write private, locally verified endpoint snapshots, without provider calls."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assistx.fleet_endpoint_harness import _read_json, build_report
from assistx.fleet_tailnet_witness import adapt_tailnet_witness


def canonical_bytes(data: dict) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest_json(data: dict) -> str:
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


def private_write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def snapshot(report: dict, directory: Path, *, private_root: Path,
             source_hashes: dict[str, str]) -> dict[str, str]:
    if private_root.is_symlink() or not private_root.is_dir():
        raise ValueError("snapshot private root missing or symlinked")
    target = directory.resolve(strict=False)
    if not target.is_relative_to(private_root.resolve(strict=True)):
        raise ValueError("snapshot path must be inside private local root")
    if directory.is_symlink():
        raise ValueError("snapshot directory symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    if directory.stat().st_dev != private_root.stat().st_dev:
        raise ValueError("snapshot would cross filesystem boundary")
    lock_path = directory / ".snapshot.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"{stamp}-{secrets.token_hex(4)}"
        report_path = directory / f"{name}.json"
        receipt_path = directory / f"{name}.receipt.json"
        payload = canonical_bytes(report)
        private_write(report_path, payload)
        receipt = {
            "schema": "fleet-endpoint-snapshot-receipt/v1",
            "report_sha256": hashlib.sha256(payload).hexdigest(),
            "sources_sha256": source_hashes,
            "local_only": True,
            "independently_archived": False,
            "upstream_billing_verified": False,
            "routing_authorized": False,
            "report_name": report_path.name,
        }
        private_write(receipt_path, canonical_bytes(receipt))
        return {
            "status": "LOCAL_SNAPSHOT_WRITTEN",
            "report": str(report_path),
            "receipt": str(receipt_path),
            "report_sha256": receipt["report_sha256"],
            "independently_archived": False,
        }
    finally:
        os.close(lock_fd)


def main(argv: list[str] | None = None) -> int:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    # On x1-370 ~/.local/state is on the 97%-full SSD_4TB, not home-root.
    # Keep new snapshots on the verified home-root filesystem instead.
    state_base = home / "git/.fleet-endpoint-harness-state"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--presence", type=Path)
    parser.add_argument("--local-observations", type=Path)
    parser.add_argument("--tailnet-witness", type=Path)
    parser.add_argument("--state-root", type=Path,
                        default=home / ".local/state/auto-assist/subagents")
    parser.add_argument("--snapshot-dir", type=Path, default=state_base / "endpoint-harness")
    parser.add_argument("--max-age-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        inventory = _read_json(args.inventory)
        presence = _read_json(args.presence) if args.presence else None
        if args.tailnet_witness and args.local_observations:
            raise ValueError("use either witness or local observations")
        local = _read_json(args.local_observations) if args.local_observations else None
        adapter_summary = None
        witness = None
        if args.tailnet_witness:
            if inventory.get("schema") != "fleet-endpoint-inventory/v1" or not isinstance(inventory.get("endpoints"), list):
                raise ValueError("invalid base inventory")
            witness = _read_json(args.tailnet_witness)
            candidates, local, adapter_summary = adapt_tailnet_witness(witness)
            inventory["endpoints"] = inventory["endpoints"] + candidates["endpoints"]
        if state_base.is_symlink():
            raise ValueError("private state root is a symlink")
        state_base.mkdir(mode=0o700, parents=True, exist_ok=True)
        if state_base.resolve(strict=True).stat().st_dev != home.stat().st_dev:
            raise ValueError("not a verified home-local state filesystem")
        os.chmod(state_base, 0o700)
        report = build_report(
            inventory, presence=presence, local_observations=local,
            state_root=args.state_root, freshness_seconds=args.max_age_seconds,
        )
        if adapter_summary is not None:
            report["tailnet_adapter"] = adapter_summary
        sources = {"inventory": digest_json(inventory)}
        if witness is not None:
            sources["tailnet_witness"] = digest_json(witness)
        if presence is not None:
            sources["presence"] = digest_json(presence)
        if local is not None:
            sources["local_observations"] = digest_json(local)
        result = snapshot(report, args.snapshot_dir, private_root=state_base, source_hashes=sources)
    except (OSError, ValueError, json.JSONDecodeError, BlockingIOError) as exc:
        print(json.dumps({"status": "REJECT", "reason": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
