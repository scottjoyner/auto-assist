#!/usr/bin/env python3
"""Read-only runtime witness collector over observed Tailnet IPv4 peers.

Tailscale membership is not model evidence; model presence is not admission.
Targets are limited to online, observed CGNAT IPv4 peers and explicitly allowed
ports. This script does not send credentials, chat requests, or mutations.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import os
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

TAILNET = ipaddress.ip_network("100.64.0.0/10")
MAX_BYTES = 65536
Fetcher = Callable[[str, float], tuple[int, bytes]]
def fetch_readonly(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
            return (413, b"") if len(body) > MAX_BYTES else (response.status, body)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b""


def decode(body: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def allowed_ports(values: list[Any]) -> list[int]:
    ports = [int(value) for value in values]
    if not ports or len(ports) > 8 or any(p < 1 or p > 65535 for p in ports):
        raise ValueError("ports must contain between 1 and 8 valid values")
    return sorted(set(ports))
def plan_targets(snapshot: dict[str, Any], ports: list[int],
                 port_map: dict[str, list[int]] | None = None,
                 max_nodes: int = 32) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if snapshot.get("authority") != "candidate_reachability_only":
        raise ValueError("expected the read-only Tailscale reconciliation inventory")
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("inventory nodes must be an array")
    selected = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("online") is not True:
            continue
        ips = node.get("tailscale_ips") or []
        valid = sorted({raw for raw in ips
                        if isinstance(raw, str) and _is_tailnet_v4(raw)})
        if not valid:
            continue
        node_id = str(node.get("node_id") or "").strip()
        if not node_id:
            continue
        selected.append((node_id, valid[0]))
    selected.sort(key=lambda item: item[0].lower())
    selected = selected[:max_nodes]
    targets = []
    for node_id, ip in selected:
        named_ports = (port_map or {}).get(node_id.lower(), [])
        for port in allowed_ports(ports + named_ports):
            targets.append({"node": node_id, "ip": ip, "port": port})
    return targets, {"online_eligible": len(selected),
                     "skipped_over_capacity": max(0, sum(
                         bool(isinstance(n, dict) and n.get("online") is True
                              and any(isinstance(ip, str) and _is_tailnet_v4(ip)
                                      for ip in n.get("tailscale_ips") or []))
                         for n in nodes) - len(selected))}


def _is_tailnet_v4(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
        return ip.version == 4 and ip in TAILNET
    except ValueError:
        return False


def _models_from_native(payload: dict[str, Any]) -> list[dict[str, str]] | None:
    entries = payload.get("models")
    if not isinstance(entries, list):
        return None
    def valid_instance(entry: dict[str, Any]) -> bool:
        instances = entry.get("loaded_instances")
        return isinstance(instances, list) and any(
            isinstance(item, dict) and isinstance(item.get("id"), str)
            and item["id"].strip() for item in instances)
    return [{"id": entry["key"],
             "state": "resident_verified" if valid_instance(entry) else "installed_not_loaded",
             "proof": "lmstudio_native_loaded_instances", "model_kind": "chat_model"}
            for entry in entries if isinstance(entry, dict) and isinstance(entry.get("key"), str)
            and entry["key"].strip() and entry.get("type") != "embedding"]
def _models_from_compatible(payload: dict[str, Any]) -> list[dict[str, str]] | None:
    entries = payload.get("data")
    if not isinstance(entries, list):
        return None
    return [{"id": entry["id"], "state": "advertised_unverified",
             "proof": "openai_v1_models", "model_kind": "unverified"}
            for entry in entries if isinstance(entry, dict) and isinstance(entry.get("id"), str)
            and entry["id"].strip()]


def inspect_target(target: dict[str, Any], fetch: Fetcher = fetch_readonly,
                   timeout: float = 1.2) -> dict[str, Any]:
    base = f"http://{target['ip']}:{target['port']}"
    trace: list[dict[str, Any]] = []
    def get(path: str) -> tuple[int, dict[str, Any] | None]:
        code, body = fetch(base + path, timeout)
        trace.append({"path": path, "http_status": code, "bytes": len(body)})
        return code, decode(body) if code == 200 else None

    native_code, native = get("/api/v1/models")
    native_models = _models_from_native(native) if native else None
    if native_models is not None:
        state = "resident_verified" if any(m["state"] == "resident_verified" for m in native_models) else (
            "installed_not_loaded" if native_models else "no_chat_models")
        return {**target, "service": "lmstudio_native", "state": state,
                "models": native_models, "trace": trace, "admitted": False}
    if native_code in (401, 403):
        return {**target, "service": "unknown", "state": "auth_required",
                "models": [], "trace": trace, "admitted": False}
    compat_code, compat = get("/v1/models")
    compatible_models = _models_from_compatible(compat) if compat else None
    if compatible_models is None:
        status = "auth_required" if compat_code in (401, 403) else (
            "unreachable" if native_code == 0 and compat_code == 0 else "incompatible_or_degraded")
        return {**target, "service": "unknown", "state": status,
                "models": [], "trace": trace, "admitted": False}
    if compatible_models:
        health_code, health = get("/health")
        props_code, props = get("/props")
        if health_code == 200 and props_code == 200 and health and props:
            alias, model_path = props.get("model_alias"), props.get("model_path")
            slots = props.get("total_slots")
            if health.get("status") == "ok" and isinstance(alias, str) and alias and (
                isinstance(model_path, str) and model_path and type(slots) is int and slots > 0):
                for model in compatible_models:
                    if model["id"] == alias:
                        model.update(state="resident_verified", proof="llamacpp_health_props")
    state = "resident_verified" if any(m["state"] == "resident_verified" for m in compatible_models) else (
        "advertised_unverified" if compatible_models else "no_chat_models")
    return {**target, "service": "openai_compatible", "state": state,
            "models": compatible_models, "trace": trace, "admitted": False}
def collect(snapshot: dict[str, Any], ports: list[int], *,
            port_map: dict[str, list[int]] | None = None, max_nodes: int = 32,
            workers: int = 4, timeout: float = 1.2,
            source_bytes: bytes | None = None,
            fetch: Fetcher = fetch_readonly) -> dict[str, Any]:
    targets, coverage = plan_targets(snapshot, ports, port_map, max_nodes)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        results = list(pool.map(lambda t: inspect_target(t, fetch, timeout), targets))
    return {"schema_version": 1, "captured_at": datetime.now(UTC).isoformat(),
            "authority": "observational_only_not_runtime_admission",
            "source_inventory_sha256": hashlib.sha256(source_bytes if source_bytes is not None
                                              else json.dumps(snapshot, sort_keys=True).encode()).hexdigest(),
            "requested_ports": ports, "coverage": {**coverage, "targets": len(targets)},
            "observations": results}


def write_atomic_private(path: Path, content: str) -> None:
    """Replace custody files atomically and avoid creating world-readable data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ports", default="1234,1235,1236")
    parser.add_argument("--port-map", type=Path, help="Trusted operator node_id -> explicit ports JSON")
    parser.add_argument("--max-nodes", type=int, default=32)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=1.2)
    args = parser.parse_args()
    if not 1 <= args.max_nodes <= 64 or not 0.2 <= args.timeout <= 5.0:
        parser.error("bounded max-nodes and timeout are required")
    ports = allowed_ports(args.ports.split(","))
    port_map = {}
    if args.port_map:
        raw = json.loads(args.port_map.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or len(raw) > 64:
            parser.error("invalid operator port map")
        port_map = {str(node).lower(): allowed_ports(values) for node, values in raw.items()
                    if isinstance(values, list)}
    raw_snapshot = args.input.read_bytes()
    snapshot = json.loads(raw_snapshot)
    result = collect(snapshot, ports, port_map=port_map, max_nodes=args.max_nodes,
                     workers=args.workers, timeout=args.timeout, source_bytes=raw_snapshot)
    data = json.dumps(result, indent=2, sort_keys=True) + "\n"
    write_atomic_private(args.output, data)
    digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
    write_atomic_private(args.output.with_suffix(args.output.suffix + ".sha256"),
                         f"{digest}  {args.output.name}\n")
    print(json.dumps({"output": str(args.output), "sha256": digest,
                      "targets": result["coverage"]["targets"],
                      "verified_resident_endpoints": sum(
                          x["state"] == "resident_verified" for x in result["observations"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())