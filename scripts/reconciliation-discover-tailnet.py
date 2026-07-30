#!/usr/bin/env python3
"""Create a read-only candidate inventory from Tailscale status.

This script discovers private-network reachability only. It never proves that a node
is an inference runtime, never loads a model, and never admits capacity. AssistX must
join these paths to independently verified physical runtime and model observations.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _load_status(path: Path | None) -> tuple[dict[str, Any], str]:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8")), str(path)
    completed = subprocess.run(
        ["tailscale", "status", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout), "tailscale status --json"


def _load_lan_map(path: Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("LAN map must be a JSON object")
    result: dict[str, list[str]] = {}
    for key, value in raw.items():
        values = value if isinstance(value, list) else [value]
        urls = [str(item).strip().rstrip("/") for item in values if str(item).strip()]
        result[str(key).strip().lower().rstrip(".")] = urls
    return result


def _node_aliases(record: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for value in (
        record.get("HostName"),
        record.get("DNSName"),
        record.get("ComputedName"),
        record.get("ComputedNameWithHost"),
    ):
        text = str(value or "").strip().lower().rstrip(".")
        if not text:
            continue
        aliases.add(text)
        aliases.add(text.split(".", 1)[0])
    for value in record.get("TailscaleIPs") or []:
        aliases.add(str(value).strip().lower())
    return aliases


def _lan_urls(record: dict[str, Any], lan_map: dict[str, list[str]]) -> list[str]:
    urls: list[str] = []
    for alias in sorted(_node_aliases(record)):
        for url in lan_map.get(alias, []):
            if url not in urls:
                urls.append(url)
    return urls


def _tailscale_urls(record: dict[str, Any], port: int) -> list[str]:
    urls: list[str] = []
    for value in record.get("TailscaleIPs") or []:
        text = str(value).strip()
        if not text:
            continue
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            continue
        host = f"[{address}]" if address.version == 6 else str(address)
        urls.append(f"http://{host}:{port}/v1")
    dns_name = str(record.get("DNSName") or "").strip().rstrip(".")
    if dns_name:
        urls.append(f"http://{dns_name}:{port}/v1")
    return list(dict.fromkeys(urls))


def _normalize_peer(record: dict[str, Any], lan_map: dict[str, list[str]], port: int) -> dict[str, Any]:
    tailscale_ips = [str(value) for value in record.get("TailscaleIPs") or []]
    access_paths: list[dict[str, Any]] = []
    for priority, url in enumerate(_lan_urls(record, lan_map), start=10):
        access_paths.append(
            {
                "transport": "lan",
                "base_url": url,
                "priority": priority,
                "source_kind": "operator_lan_map",
            }
        )
    for priority, url in enumerate(_tailscale_urls(record, port), start=100):
        access_paths.append(
            {
                "transport": "tailscale",
                "base_url": url,
                "priority": priority,
                "source_kind": "tailscale_status",
            }
        )
    return {
        "node_id": str(
            record.get("HostName")
            or record.get("ComputedName")
            or record.get("DNSName")
            or record.get("ID")
            or "unknown"
        ),
        "tailscale_node_id": record.get("ID"),
        "dns_name": str(record.get("DNSName") or "").rstrip("."),
        "tailscale_ips": tailscale_ips,
        "online": bool(record.get("Online", False)),
        "active": bool(record.get("Active", False)),
        "last_seen": record.get("LastSeen"),
        "candidate_access_paths": access_paths,
        "admission_status": "candidate_only",
        "required_before_admission": [
            "physical_runtime_identity",
            "loaded_model_instance",
            "official_lms_process_evidence",
            "explicit_parallel_slots",
            "completion_canary",
        ],
    }


def _peer_records(status: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    self_record = status.get("Self")
    if isinstance(self_record, dict):
        records.append(self_record)
    peers = status.get("Peer") or {}
    if isinstance(peers, dict):
        records.extend(item for item in peers.values() if isinstance(item, dict))
    elif isinstance(peers, list):
        records.extend(item for item in peers if isinstance(item, dict))
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="Existing tailscale status --json output")
    parser.add_argument("--lan-map", type=Path, help="JSON hostname-to-LAN-URL mapping")
    parser.add_argument("--lmstudio-port", type=int, default=1234)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/reconciliation-tailnet-candidates.json"),
    )
    args = parser.parse_args()

    status, source = _load_status(args.input)
    lan_map = _load_lan_map(args.lan_map)
    nodes = [
        _normalize_peer(record, lan_map, args.lmstudio_port)
        for record in _peer_records(status)
    ]
    nodes.sort(key=lambda item: (not item["online"], str(item["node_id"]).lower()))
    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "source": source,
        "authority": "candidate_reachability_only",
        "lan_preferred": True,
        "tailscale_fallback": True,
        "nodes": nodes,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.write_text(serialized, encoding="utf-8")
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    checksum_path = args.output.with_suffix(args.output.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {args.output.name}\n", encoding="utf-8")
    print(args.output)
    print(checksum_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
