#!/usr/bin/env python3
"""Read-only, fail-closed diagnostic witness for Kipnerter mobile inference.

No credentials, mutations, model loads, requests for chat completions or automatic
approval. Never treat model inventory as residency or a healthy gateway as admission.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

MAX_BODY = 65536


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def origin(raw: str, *, https_only: bool = False) -> str:
    parsed = urllib.parse.urlsplit(raw.strip())
    if parsed.scheme.lower() not in (("https",) if https_only else ("http", "https")):
        raise ValueError("unsupported endpoint scheme")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("endpoint must contain only a clean host and optional port/path")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid port") from exc
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("invalid port")
    if parsed.path not in ("", "/"):
        raise ValueError("endpoint must be an origin, not an API path")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def get_json(base: str, path: str, timeout: float) -> tuple[int | None, dict[str, Any] | None]:
    # URL origin was validated; paths are code-owned literals, not remote values.
    request = urllib.request.Request(
        base + path, headers={"Accept": "application/json"}, method="GET"
    )
    opener = urllib.request.build_opener(NoRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            code = response.status
            body = response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, None  # no server body or transport details in witness
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, None
    if len(body) > MAX_BODY:
        return code, None
    try:
        document = json.loads(body)
        return code, document if isinstance(document, dict) else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return code, None


def gateway_status(base: str, timeout: float = 3.0,
                   fetch: Callable = get_json) -> dict[str, Any]:
    health_code, _ = fetch(base, "/health", timeout)
    catalog_code, catalog = fetch(base, "/api/v1/runtime/catalog", timeout)
    admitted = (
        health_code == 200
        and catalog_code == 200
        and isinstance(catalog, dict)
        and catalog.get("schema_version") in ("1", "2")
        and catalog.get("agent_auto_available") is True
        and isinstance(catalog.get("agent_runtime_count"), int)
        and catalog["agent_runtime_count"] > 0
    )
    reason = (
        "admitted" if admitted else
        "gateway_unreachable" if health_code is None else
        "gateway_health_error" if health_code != 200 else
        "identity_required" if catalog_code in (401, 403) else
        "catalog_transport_error" if catalog_code is None else
        "catalog_http_error" if catalog_code != 200 else
        "projection_not_admitted"
    )
    return {
        "health_http": health_code, "catalog_http": catalog_code,
        "state": reason, "agent_auto_admitted": admitted,
        "fleet_runtime_count": (catalog.get("fleet_runtime_count") if catalog_code == 200
                                and isinstance(catalog, dict) and isinstance(catalog.get("fleet_runtime_count"), int)
                                else None),
        "agent_runtime_count": (catalog.get("agent_runtime_count") if catalog_code == 200
                                and isinstance(catalog, dict) and isinstance(catalog.get("agent_runtime_count"), int)
                                else None),
    }


def provider_status(base: str, timeout: float = 3.0,
                    fetch: Callable = get_json) -> dict[str, Any]:
    native_http, native = fetch(base, "/api/v1/models", timeout)
    if native_http == 200 and isinstance(native, dict) and isinstance(native.get("models"), list):
        models = [m for m in native["models"]
                  if isinstance(m, dict) and m.get("type") != "embedding"]
        loaded = [m for m in models if isinstance(m.get("loaded_instances"), list)
                  and len(m["loaded_instances"]) > 0]
        return {"provider": base, "protocol": "lmstudio_native",
                "models_listed": len(models), "confirmed_loaded": len(loaded),
                "state": "resident" if loaded else "inventory_only"}

    compatible_http, compatible = fetch(base, "/v1/models", timeout)
    if compatible_http != 200 or not isinstance(compatible, dict) or not isinstance(compatible.get("data"), list):
        return {"provider": base, "protocol": "unknown",
                "models_listed": 0, "confirmed_loaded": 0, "state": "unreachable_or_incompatible"}
    models = [m for m in compatible["data"] if isinstance(m, dict) and isinstance(m.get("id"), str)]
    health_code, health = fetch(base, "/health", timeout)
    props_code, props = fetch(base, "/props", timeout)
    witness = (
        health_code == props_code == 200
        and isinstance(health, dict) and health.get("status") == "ok"
        and isinstance(props, dict)
        and isinstance(props.get("model_alias"), str)
        and isinstance(props.get("model_path"), str)
        and bool(props["model_path"].strip())
        and isinstance(props.get("total_slots"), int)
        and props["total_slots"] > 0
    )
    ids = {m["id"] for m in models}
    confirmed = 1 if witness and props["model_alias"] in ids else 0
    return {"provider": base, "protocol": "compatible_with_llamacpp_proof" if confirmed else "compatible_unproven",
            "models_listed": len(models), "confirmed_loaded": confirmed,
            "state": "resident" if confirmed else "inventory_only"}


def observe(gateway: str, providers: list[str], timeout: float = 3.0,
            fetch: Callable = get_json) -> dict[str, Any]:
    g = origin(gateway, https_only=True)
    ps = list(dict.fromkeys(origin(p) for p in providers))
    result: dict[str, Any] = {
        "schema_version": 1,
        "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "gateway": g,
        "gateway_witness": gateway_status(g, timeout, fetch),
        "provider_witnesses": [provider_status(p, timeout, fetch) for p in ps],
        "boundary": "observation_only_no_admission_no_model_load",
    }
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    result["record_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="https://x1-370.tailcb8954.ts.net:8443")
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--require-admitted", action="store_true")
    args = parser.parse_args(argv)
    if not (0.5 <= args.timeout <= 8):
        parser.error("timeout must be between 0.5 and 8 seconds")
    try:
        result = observe(args.gateway, args.provider, args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    sys.stdout.write(rendered)
    return 0 if (not args.require_admitted or result["gateway_witness"]["agent_auto_admitted"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
