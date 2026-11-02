#!/usr/bin/env python3
"""Observe private model-serving paths; never issue admission or lease evidence.

An API model listing is not physical runtime attestation. Outputs are deliberately
NON-ADMITTING until a separate source-attested, operator-reviewed approval.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

TAILNET = ipaddress.ip_network("100.64.0.0/10")
MAX_BYTES = 512_000


class UnsafeEndpoint(ValueError):
    pass


def private_url(url: str, *, transport: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise UnsafeEndpoint("endpoint requires HTTP(S) and a host")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UnsafeEndpoint("credentials, query and fragment are forbidden")
    try:
        if parts.port is None or not 1 <= parts.port <= 65535:
            raise UnsafeEndpoint("endpoint requires an explicit valid TCP port")
    except ValueError as exc:
        raise UnsafeEndpoint("endpoint has invalid TCP port") from exc
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError as exc:
        raise UnsafeEndpoint("endpoint must use a numeric IP address") from exc
    if transport == "lan":
        if not (address.is_private and address not in TAILNET and not address.is_loopback):
            raise UnsafeEndpoint("LAN path must use a non-loopback private IP outside Tailnet")
    elif transport == "tailscale":
        if address not in TAILNET:
            raise UnsafeEndpoint("Tailscale path must be in 100.64.0.0/10")
    else:
        raise UnsafeEndpoint("unsupported transport")
    if parts.path not in {"", "/", "/v1", "/v1/"}:
        raise UnsafeEndpoint("endpoint must target the API root")
    return url.rstrip("/").removesuffix("/v1") + "/v1"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UnsafeEndpoint("HTTP redirects are forbidden for admission probes")


def get_json(url: str, timeout: float) -> dict:
    # Never inherit environment HTTP proxies for private fleet evidence.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
        if int(response.status) != 200:
            raise ValueError(f"HTTP {response.status}")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("response exceeds limit")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("response must be an object")
    return value


def probe_target(row: dict, *, timeout: float = 4) -> dict:
    node = str(row.get("node_id") or "").strip()
    expected = str(row.get("expected_model") or "").strip()
    result = {
        "node_id": node,
        "expected_model": expected,
        "admission_applied": False,
        "physical_attestation": False,
        "operator_approved": False,
        "status": "blocked",
        "paths": {},
        "reasons": [],
    }
    if not node or not expected:
        result["reasons"].append("node_id and expected_model required")
        return result
    paths = row.get("access_paths")
    if not isinstance(paths, dict) or set(paths) != {"lan", "tailscale"}:
        result["reasons"].append("both LAN and Tailscale paths required")
        return result
    urls = {}
    for transport in ("lan", "tailscale"):
        try:
            urls[transport] = private_url(str(paths[transport]), transport=transport)
        except (UnsafeEndpoint, ValueError) as exc:
            result["reasons"].append(f"{transport}: {exc}")
    if len(urls) != 2:
        return result
    for transport, url in urls.items():
        entry = {"reachable": False, "model_advertised": False}
        try:
            data = get_json(url + "/models", timeout)
            items = data.get("data", [])
            if not isinstance(items, list):
                raise ValueError("model inventory data must be a list")
            models = {
                item["id"] for item in items
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            entry["reachable"] = True
            entry["model_advertised"] = expected in models
            # Useful operator diagnostics without turning an arbitrary model
            # listing into a signed claim. Bound content from untrusted APIs.
            # The full inventory (not this capped display) decides exact match.
            entry["advertised_model_count"] = len(models)
            entry["advertised_model_ids"] = sorted(
                model for model in models
                if 0 < len(model) <= 160
                and all(32 <= ord(char) < 127 for char in model)
            )[:8]
            entry["advertised_model_ids_truncated"] = (
                len(models) > len(entry["advertised_model_ids"])
            )
            if not entry["model_advertised"]:
                result["reasons"].append(f"{transport}: exact model ID not advertised")
        except Exception as exc:
            result["reasons"].append(f"{transport}: probe failed ({type(exc).__name__})")
        result["paths"][transport] = entry
    if all(p["model_advertised"] for p in result["paths"].values()):
        # This is only an API-visible readiness observation, NOT admission.
        result["status"] = "api_visible_only"
        result["reasons"].append("physical artifact, live PID, capacity and operator attestation still required")
    return result


def build_report(rows: list[dict], *, timeout: float = 4) -> dict:
    seen = set()
    results = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("targets must be objects")
        node = str(row.get("node_id") or "").strip()
        if node in seen:
            raise ValueError(f"duplicate node_id: {node}")
        seen.add(node)
        results.append(probe_target(row, timeout=timeout))
    issued_at = datetime.now(timezone.utc).isoformat()
    report = {
        "schema_version": 1,
        "collected_at_utc": issued_at,
        "mode": "read_only",
        "admission_applied": False,
        "not_an_approval": True,
        "nodes": results,
        "api_visible_count": sum(r["status"] == "api_visible_only" for r in results),
        "blocked_count": sum(r["status"] == "blocked" for r in results),
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["observation_sha256"] = hashlib.sha256(encoded).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", required=True, help="local JSON file of vetted private endpoints")
    parser.add_argument("--out", required=True, help="new evidence-only JSON output")
    parser.add_argument("--timeout", type=float, default=4)
    args = parser.parse_args()
    if not 0.2 <= args.timeout <= 15:
        parser.error("timeout must be 0.2 to 15 seconds")
    source = Path(args.targets).resolve()
    destination = Path(args.out).resolve()
    if source.is_symlink() or not source.is_file():
        parser.error("targets must be a regular file")
    if destination.exists():
        parser.error("output must not already exist (immutable run evidence)")
    data = json.loads(source.read_text())
    if not isinstance(data, list) or not data:
        parser.error("targets must be a non-empty JSON array")
    report = build_report(data, timeout=args.timeout)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(destination, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"out": str(destination), "api_visible": report["api_visible_count"],
                      "blocked": report["blocked_count"], "admission_applied": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
