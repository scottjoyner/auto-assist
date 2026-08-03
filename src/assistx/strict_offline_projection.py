from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlparse


_PUBLIC_TOKENS = {
    "openrouter",
    "cerebras",
    "groq",
    "grok",
    "xai",
    "anthropic",
    "gemini",
    "mistral",
    "cloudflare",
}
_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def _private_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https", "bolt", "redis"}:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
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
        or address in _TAILSCALE
    )


def _serialized(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            f"{key} {_serialized(item)}" for key, item in value.items()
        ).lower()
    if isinstance(value, list):
        return " ".join(_serialized(item) for item in value).lower()
    return str(value or "").lower()


def _provider_allowed(provider: Any) -> bool:
    if not isinstance(provider, dict):
        return False
    serialized = _serialized(provider)
    if any(token in serialized for token in _PUBLIC_TOKENS):
        return False
    if provider.get("can_use_free_api") is True:
        return False
    if str(provider.get("lane") or "").lower() in {
        "free_api",
        "paid_api",
        "heavy_reasoning",
        "paperclip",
    }:
        return False
    if provider.get("local") is False:
        return False
    for service in provider.get("services") or []:
        if isinstance(service, dict) and not _private_url(
            service.get("url") or service.get("base_url")
        ):
            return False
    return True


def _node_allowed(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    serialized = _serialized(node)
    if any(token in serialized for token in _PUBLIC_TOKENS):
        return False
    if str(node.get("node_id") or "").lower() == "paperclip":
        return False
    if str(node.get("lane") or "").lower() in {
        "free_api",
        "paid_api",
        "paperclip",
    }:
        return False
    for service in node.get("services") or []:
        if isinstance(service, dict) and not _private_url(
            service.get("url") or service.get("base_url")
        ):
            return False
    return True


def _service_allowed(service: Any) -> bool:
    if not isinstance(service, dict):
        return False
    serialized = _serialized(service)
    if any(token in serialized for token in _PUBLIC_TOKENS):
        return False
    if "paperclip" in serialized:
        return False
    return _private_url(service.get("url") or service.get("base_url"))


def install_strict_offline_projection(router_integration_module: Any) -> None:
    if os.getenv("ASSISTX_STRICT_OFFLINE", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    if getattr(router_integration_module, "_strict_offline_projection_installed", False):
        return

    original_providers = router_integration_module._provider_projection
    original_nodes = router_integration_module._node_projection
    original_services = router_integration_module._service_projection
    original_merge = router_integration_module._merge_providers

    def providers(base_url: str) -> list[dict[str, Any]]:
        return [item for item in original_providers(base_url) if _provider_allowed(item)]

    def nodes(base_url: str, graph: dict[str, Any]) -> list[dict[str, Any]]:
        return [item for item in original_nodes(base_url, graph) if _node_allowed(item)]

    def services(base_url: str) -> list[dict[str, Any]]:
        return [item for item in original_services(base_url) if _service_allowed(item)]

    def merge(
        static: list[dict[str, Any]],
        live: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in original_merge(static, live)
            if _provider_allowed(item)
        ]

    router_integration_module._provider_projection = providers
    router_integration_module._node_projection = nodes
    router_integration_module._service_projection = services
    router_integration_module._merge_providers = merge
    router_integration_module._strict_offline_projection_installed = True
