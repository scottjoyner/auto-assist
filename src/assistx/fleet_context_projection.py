from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

_INSTALLED = False


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return sorted({str(item) for item in value if str(item).strip()})
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return _json_list(parsed)
    return []


def fleet_nodes(neo_factory: Callable[[], Any]) -> list[dict[str, Any]]:
    neo = neo_factory()
    try:
        with neo._session() as session:
            rows = session.run(
                """
                MATCH (n:FleetNode)
                WHERE coalesce(n.tailnet_discovered, false) = true
                RETURN properties(n) AS node
                ORDER BY n.online DESC, n.node_id
                """
            )
            result: list[dict[str, Any]] = []
            for row in rows:
                node = dict(row["node"])
                worker_mode = str(node.get("worker_mode") or "observer_only")
                capabilities = set(_json_list(node.get("capabilities_json")))
                capabilities.update(_json_list(node.get("roles_json")))
                tailscale_ips = _json_list(node.get("tailscale_ips_json"))
                details = [f"tailnet worker mode: {worker_mode}"]
                if node.get("dns_name"):
                    details.append(str(node["dns_name"]))
                if tailscale_ips:
                    details.append(", ".join(tailscale_ips))
                result.append(
                    {
                        "node_id": str(node.get("node_id") or ""),
                        "display_name": str(
                            node.get("display_name") or node.get("node_id") or ""
                        ),
                        "lane": "blocked" if worker_mode == "observer_only" else "local",
                        "local": True,
                        "can_use_free_api": False,
                        "running": bool(node.get("online", False)),
                        "capabilities": sorted(capabilities),
                        "detail": "; ".join(details),
                        "services": [],
                        "metadata": {
                            "tailnet_discovered": True,
                            "worker_mode": worker_mode,
                            "allow_agent_runtime": bool(
                                node.get("allow_agent_runtime", False)
                            ),
                            "allow_code_execution": bool(
                                node.get("allow_code_execution", False)
                            ),
                            "tailscale_ips": tailscale_ips,
                            "dns_name": node.get("dns_name"),
                            "benchmark_policy": node.get("benchmark_policy"),
                            "energy_class": node.get("energy_class"),
                        },
                    }
                )
            return result
    finally:
        neo.close()


def merge_nodes(
    static_nodes: list[dict[str, Any]],
    discovered_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for node in static_nodes + discovered_nodes:
        node_id = str(node.get("node_id") or "").strip()
        if not node_id:
            continue
        previous = merged.get(node_id)
        if previous is None:
            merged[node_id] = node
            continue
        # Tailnet evidence enriches a pre-existing service node without erasing
        # service endpoints or stronger component-specific capabilities.
        capabilities = sorted(
            set(previous.get("capabilities") or [])
            | set(node.get("capabilities") or [])
        )
        services = list(previous.get("services") or []) + list(node.get("services") or [])
        merged[node_id] = {
            **previous,
            **node,
            "capabilities": capabilities,
            "services": services,
            "running": bool(previous.get("running") or node.get("running")),
        }
    return sorted(
        merged.values(),
        key=lambda item: (not bool(item.get("running")), str(item.get("node_id"))),
    )


def install_fleet_node_context_projection(
    router_integration_module: Any,
    neo_factory: Callable[[], Any],
) -> None:
    """Add all imported Tailscale nodes to the read-only router context projection."""

    global _INSTALLED
    if _INSTALLED:
        return
    original = router_integration_module._node_projection

    def projected_nodes(base_url: str, graph: dict[str, Any]) -> list[dict[str, Any]]:
        static = original(base_url, graph)
        try:
            discovered = fleet_nodes(neo_factory)
        except Exception:
            discovered = []
        return merge_nodes(static, discovered)

    router_integration_module._node_projection = projected_nodes
    _INSTALLED = True
