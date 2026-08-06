from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

SCHEMA_VERSION = "fleet_routing_matrix.v1"


class FleetRoutingMatrixError(ValueError):
    pass


def _canonical(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def matrix_fingerprint(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def _list_of_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def validate_matrix(document: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(document)
    if value.get("schema_version") != SCHEMA_VERSION:
        raise FleetRoutingMatrixError(f"routing matrix schema must be {SCHEMA_VERSION}")
    nodes = value.get("nodes")
    profiles = value.get("profiles")
    rankings = value.get("rankings")
    if not isinstance(nodes, list) or not nodes:
        raise FleetRoutingMatrixError("routing matrix requires a non-empty nodes array")
    if not isinstance(profiles, list):
        raise FleetRoutingMatrixError("routing matrix profiles must be an array")
    if not isinstance(rankings, Mapping):
        raise FleetRoutingMatrixError("routing matrix rankings must be an object")

    normalized_nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for raw in nodes:
        if not isinstance(raw, Mapping):
            raise FleetRoutingMatrixError("routing matrix node rows must be objects")
        node = dict(raw)
        node_id = str(node.get("node_id") or "").strip()
        if not node_id:
            raise FleetRoutingMatrixError("routing matrix node_id is required")
        if node_id in node_ids:
            raise FleetRoutingMatrixError(f"duplicate routing matrix node {node_id}")
        node_ids.add(node_id)
        node["roles"] = _list_of_strings(node.get("roles"))
        node["capabilities"] = _list_of_strings(node.get("capabilities"))
        node["tailscale_ips"] = _list_of_strings(node.get("tailscale_ips"))
        node["tags"] = _list_of_strings(node.get("tags"))
        node["worker_mode"] = str(node.get("worker_mode") or "observer_only")
        node["tailnet_discovered"] = bool(node.get("tailnet_discovered", True))
        node["allow_agent_runtime"] = bool(node.get("allow_agent_runtime", False))
        node["allow_code_execution"] = bool(node.get("allow_code_execution", False))
        node["online"] = bool(node.get("online", False))
        node["max_concurrent"] = max(0, int(node.get("max_concurrent") or 0))
        normalized_nodes.append(node)

    normalized_profiles: list[dict[str, Any]] = []
    profile_ids: set[str] = set()
    for raw in profiles:
        if not isinstance(raw, Mapping):
            raise FleetRoutingMatrixError("routing matrix profile rows must be objects")
        profile = dict(raw)
        node_id = str(profile.get("node_id") or "").strip()
        model_id = str(profile.get("model_id") or "").strip()
        family = str(profile.get("task_family") or "").strip().lower()
        if node_id not in node_ids or not model_id or not family:
            raise FleetRoutingMatrixError("routing profile requires known node_id, model_id, and task_family")
        profile_id = hashlib.sha256(f"{node_id}|{model_id}|{family}".encode()).hexdigest()
        if profile_id in profile_ids:
            raise FleetRoutingMatrixError(
                f"duplicate routing profile for {node_id}/{model_id}/{family}"
            )
        profile_ids.add(profile_id)
        profile["profile_id"] = profile_id
        for field, default in (
            ("quality_score", 0.5),
            ("quality_confidence", 0.0),
            ("reliability", 0.5),
            ("speed_score", 0.15),
            ("utility_score", 0.0),
            ("quality_floor", 0.0),
        ):
            try:
                profile[field] = float(profile.get(field, default))
            except (TypeError, ValueError) as exc:
                raise FleetRoutingMatrixError(
                    f"routing profile {field} must be numeric"
                ) from exc
        tps = profile.get("tokens_per_second")
        profile["tokens_per_second"] = float(tps) if tps is not None else None
        profile["quality_floor_passed"] = bool(profile.get("quality_floor_passed", False))
        profile["roles"] = _list_of_strings(profile.get("roles"))
        normalized_profiles.append(profile)

    value["nodes"] = normalized_nodes
    value["profiles"] = normalized_profiles
    value["rankings"] = {str(key): list(rows) for key, rows in rankings.items() if isinstance(rows, list)}
    return value


def import_matrix(
    neo_factory: Callable[[], Any],
    document: Mapping[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    value = validate_matrix(document)
    fingerprint = matrix_fingerprint(value)
    imported_at = int(time.time() * 1000)
    neo = neo_factory()
    try:
        with neo._session() as session:
            session.run(
                """
                MERGE (s:FleetRoutingMatrixState {name:'canonical'})
                SET s.schema_version = $schema_version,
                    s.matrix_fingerprint = $matrix_fingerprint,
                    s.generated_at_utc = $generated_at_utc,
                    s.imported_at_ts = $imported_at_ts,
                    s.imported_by = $actor,
                    s.summary_json = $summary_json,
                    s.policy_json = $policy_json
                """,
                {
                    "schema_version": SCHEMA_VERSION,
                    "matrix_fingerprint": fingerprint,
                    "generated_at_utc": str(value.get("generated_at_utc") or ""),
                    "imported_at_ts": imported_at,
                    "actor": actor,
                    "summary_json": json.dumps(value.get("summary") or {}, sort_keys=True),
                    "policy_json": json.dumps(value.get("policy") or {}, sort_keys=True),
                },
            )
            for node in value["nodes"]:
                session.run(
                    """
                    MERGE (n:FleetNode {node_id:$node_id})
                    SET n.display_name = $display_name,
                        n.dns_name = $dns_name,
                        n.tailscale_ips_json = $tailscale_ips_json,
                        n.os_family = $os_family,
                        n.online = $online,
                        n.active = $active,
                        n.last_seen = $last_seen,
                        n.tags_json = $tags_json,
                        n.tailnet_discovered = $tailnet_discovered,
                        n.discovery_source = $discovery_source,
                        n.roles_json = $roles_json,
                        n.capabilities_json = $capabilities_json,
                        n.worker_mode = $worker_mode,
                        n.allow_agent_runtime = $allow_agent_runtime,
                        n.allow_code_execution = $allow_code_execution,
                        n.benchmark_policy = $benchmark_policy,
                        n.max_concurrent = $max_concurrent,
                        n.energy_class = $energy_class,
                        n.notes = $notes,
                        n.routing_matrix_fingerprint = $matrix_fingerprint,
                        n.routing_matrix_imported_at_ts = $imported_at_ts
                    """,
                    {
                        **node,
                        "tailscale_ips_json": json.dumps(node["tailscale_ips"]),
                        "tags_json": json.dumps(node["tags"]),
                        "roles_json": json.dumps(node["roles"]),
                        "capabilities_json": json.dumps(node["capabilities"]),
                        "display_name": str(node.get("display_name") or node["node_id"]),
                        "dns_name": node.get("dns_name"),
                        "os_family": str(node.get("os_family") or "unknown"),
                        "active": bool(node.get("active", False)),
                        "last_seen": node.get("last_seen"),
                        "discovery_source": str(node.get("discovery_source") or "tailscale-status-json"),
                        "benchmark_policy": str(node.get("benchmark_policy") or "inventory_only"),
                        "energy_class": str(node.get("energy_class") or "unknown"),
                        "notes": str(node.get("notes") or ""),
                        "matrix_fingerprint": fingerprint,
                        "imported_at_ts": imported_at,
                    },
                )
            session.run(
                """
                MATCH (p:BenchmarkRoutingProfile)
                WHERE p.matrix_fingerprint <> $matrix_fingerprint
                DETACH DELETE p
                """,
                {"matrix_fingerprint": fingerprint},
            )
            for profile in value["profiles"]:
                session.run(
                    """
                    MATCH (n:FleetNode {node_id:$node_id})
                    MERGE (p:BenchmarkRoutingProfile {profile_id:$profile_id})
                    SET p.node_id = $node_id,
                        p.model_id = $model_id,
                        p.task_family = $task_family,
                        p.loadout_fingerprint = $loadout_fingerprint,
                        p.qualified = $qualified,
                        p.quality_score = $quality_score,
                        p.quality_confidence = $quality_confidence,
                        p.reliability = $reliability,
                        p.tokens_per_second = $tokens_per_second,
                        p.speed_score = $speed_score,
                        p.utility_score = $utility_score,
                        p.quality_floor = $quality_floor,
                        p.quality_floor_passed = $quality_floor_passed,
                        p.worker_mode = $worker_mode,
                        p.roles_json = $roles_json,
                        p.source_schema = $source_schema,
                        p.matrix_fingerprint = $matrix_fingerprint,
                        p.imported_at_ts = $imported_at_ts
                    MERGE (n)-[:HAS_BENCHMARK_PROFILE]->(p)
                    """,
                    {
                        **profile,
                        "loadout_fingerprint": profile.get("loadout_fingerprint"),
                        "qualified": bool(profile.get("qualified", False)),
                        "roles_json": json.dumps(profile["roles"]),
                        "source_schema": str(profile.get("source_schema") or "unknown"),
                        "matrix_fingerprint": fingerprint,
                        "imported_at_ts": imported_at,
                    },
                )
    finally:
        neo.close()
    return {
        "imported": True,
        "schema_version": SCHEMA_VERSION,
        "matrix_fingerprint": fingerprint,
        "nodes": len(value["nodes"]),
        "online_nodes": sum(bool(node["online"]) for node in value["nodes"]),
        "profiles": len(value["profiles"]),
        "observer_only_nodes": sum(
            node["worker_mode"] == "observer_only" for node in value["nodes"]
        ),
    }


def current_matrix(neo_factory: Callable[[], Any]) -> dict[str, Any]:
    neo = neo_factory()
    try:
        with neo._session() as session:
            state = session.run(
                """
                MATCH (s:FleetRoutingMatrixState {name:'canonical'})
                RETURN properties(s) AS state
                LIMIT 1
                """
            ).single()
            nodes = [
                dict(row["node"])
                for row in session.run(
                    """
                    MATCH (n:FleetNode)
                    RETURN properties(n) AS node
                    ORDER BY n.online DESC, n.node_id
                    """
                )
            ]
            profiles = [
                dict(row["profile"])
                for row in session.run(
                    """
                    MATCH (p:BenchmarkRoutingProfile)
                    RETURN properties(p) AS profile
                    ORDER BY p.task_family, p.utility_score DESC
                    """
                )
            ]
    finally:
        neo.close()
    return {
        "state": dict(state["state"]) if state else None,
        "nodes": nodes,
        "profiles": profiles,
    }


def benchmark_projection_index(
    neo_factory: Callable[[], Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    neo = neo_factory()
    try:
        with neo._session() as session:
            rows = session.run(
                """
                MATCH (n:FleetNode)-[:HAS_BENCHMARK_PROFILE]->(p:BenchmarkRoutingProfile)
                WHERE coalesce(p.quality_floor_passed, false) = true
                RETURN n.node_id AS node_id,
                       n.roles_json AS roles_json,
                       n.worker_mode AS worker_mode,
                       n.allow_agent_runtime AS allow_agent_runtime,
                       n.allow_code_execution AS allow_code_execution,
                       properties(p) AS profile
                """
            )
            grouped: dict[tuple[str, str], dict[str, Any]] = {}
            for row in rows:
                profile = dict(row["profile"])
                key = (str(row["node_id"]), str(profile.get("model_id") or ""))
                if not key[1]:
                    continue
                entry = grouped.setdefault(
                    key,
                    {
                        "routing_roles": _json_list(row.get("roles_json")),
                        "worker_mode": str(row.get("worker_mode") or "observer_only"),
                        "allow_agent_runtime": bool(row.get("allow_agent_runtime", False)),
                        "allow_code_execution": bool(row.get("allow_code_execution", False)),
                        "task_family_scores": {},
                    },
                )
                family = str(profile.get("task_family") or "")
                entry["task_family_scores"][family] = {
                    "quality_score": profile.get("quality_score"),
                    "quality_confidence": profile.get("quality_confidence"),
                    "reliability": profile.get("reliability"),
                    "tokens_per_second": profile.get("tokens_per_second"),
                    "speed_score": profile.get("speed_score"),
                    "utility_score": profile.get("utility_score"),
                    "quality_floor": profile.get("quality_floor"),
                    "quality_floor_passed": profile.get("quality_floor_passed"),
                    "loadout_fingerprint": profile.get("loadout_fingerprint"),
                }
            return grouped
    finally:
        neo.close()


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _list_of_strings(value)
    if isinstance(value, str) and value.strip():
        try:
            return _list_of_strings(json.loads(value))
        except json.JSONDecodeError:
            return []
    return []


def build_fleet_routing_matrix_router(
    neo_factory: Callable[[], Any],
    auth_dependency: Any,
) -> APIRouter:
    router = APIRouter(prefix="/api/fleet/routing-matrix", tags=["fleet-routing"])

    @router.post("/import", dependencies=[Depends(auth_dependency)])
    def import_document(document: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return import_matrix(neo_factory, document, actor="operator:api")
        except (FleetRoutingMatrixError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("", dependencies=[Depends(auth_dependency)])
    def get_current() -> dict[str, Any]:
        return current_matrix(neo_factory)

    return router
