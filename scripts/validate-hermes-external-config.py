#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import pathlib
import sys
from typing import Any
from urllib.parse import urlparse

import yaml


TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def private_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "host.docker.internal", "gateway.docker.internal"}:
        return True
    if host.endswith((".lan", ".local", ".internal", ".ts.net")):
        return True
    if "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address in TAILSCALE
    )


def validate(payload: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    fleet = mapping(payload.get("fleet"))
    if str(fleet.get("mode") or "").strip().lower() != "external":
        failures.append("fleet.mode must be external")
    if fleet.get("nodes"):
        failures.append("fleet.nodes is forbidden in the reconciled deployment")
    external = mapping(fleet.get("external"))
    base_url = str(external.get("base_url") or "").strip().rstrip("/")
    admin_url = str(external.get("admin_url") or "").strip().rstrip("/")
    if not private_url(base_url):
        failures.append("fleet.external.base_url must be a private http(s) URL")
    if not private_url(admin_url):
        failures.append("fleet.external.admin_url must be a private http(s) URL")
    if not base_url.endswith("/v1"):
        failures.append("fleet.external.base_url must terminate at /v1")
    if str(external.get("strict_offline", "")).strip().lower() not in {
        "true",
        "1",
        "yes",
        "on",
    } and external.get("strict_offline") is not True:
        failures.append("fleet.external.strict_offline must be true")
    if not str(external.get("admin_token_env") or "").strip():
        failures.append("fleet.external.admin_token_env is required")
    default_model = str(external.get("default_model") or "").strip()
    if not default_model.startswith("auto/"):
        failures.append("fleet.external.default_model must be an auto/* intent alias")

    model = mapping(payload.get("model"))
    model_base_url = str(model.get("base_url") or "").strip().rstrip("/")
    if model_base_url != base_url:
        failures.append("model.base_url must equal fleet.external.base_url")
    if str(model.get("default") or "").strip() != default_model:
        failures.append("model.default must equal fleet.external.default_model")

    plugins = mapping(payload.get("plugins"))
    enabled = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    if "fleet-router" not in enabled:
        failures.append("fleet-router plugin must be enabled")

    if int(payload.get("max_concurrent_sessions") or 0) != 1:
        failures.append("max_concurrent_sessions must be 1 for the initial cutover")
    return sorted(set(failures))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the reconciled Hermes external auto-router configuration."
    )
    parser.add_argument("config", type=pathlib.Path)
    args = parser.parse_args()
    try:
        payload = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"HERMES_EXTERNAL_CONFIG: BLOCKED {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("HERMES_EXTERNAL_CONFIG: BLOCKED root must be a mapping", file=sys.stderr)
        return 2
    failures = validate(payload)
    if failures:
        print("HERMES_EXTERNAL_CONFIG: BLOCKED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("HERMES_EXTERNAL_CONFIG: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
