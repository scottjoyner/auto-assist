from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import degraded_control_plane as control
from .recovery_snapshot import private_http_url

_INSTALLED = False
_ORIGINAL_PLAN = control.DegradedControlPlaneRuntime.plan_delegation


def _private_local_provider(provider: Mapping[str, Any]) -> bool:
    quota = str(provider.get("quota_class") or "local").strip().lower()
    provider_type = str(provider.get("type") or "").strip().lower()
    urls = [
        str(value or "").strip()
        for value in (
            provider.get("access_urls") or [provider.get("base_url")]
        )
        if str(value or "").strip()
    ]
    return bool(
        provider.get("enabled", True)
        and quota in {"local", "private"}
        and provider_type
        in {
            "lmstudio",
            "llama_cpp",
            "vllm",
            "sglang",
            "openai_compatible",
        }
        and urls
        and all(private_http_url(url) for url in urls)
    )


def _fresh_heartbeat_plan(self, body: Mapping[str, Any]) -> dict[str, Any]:
    task_id = control._required_text(body.get("task_id"), "task_id")
    owner = control._required_text(body.get("owner"), "owner")
    epoch = control._required_epoch(body.get("epoch"))
    required = {
        str(value)
        for value in body.get("required_capabilities") or []
        if str(value)
    }
    excluded = {
        str(value)
        for value in body.get("excluded_nodes") or []
        if str(value)
    }
    projection = self.get_runtime_projection()
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for provider in projection.get("providers") or []:
        node_id = str(provider.get("node_id") or "")
        if (
            not node_id
            or node_id in excluded
            or not _private_local_provider(provider)
        ):
            continue
        capabilities = {
            str(capability)
            for model in provider.get("models") or []
            for capability in model.get("capabilities") or []
        }
        if not required.issubset(capabilities):
            continue
        heartbeat = self.store.get("heartbeat", node_id)
        if heartbeat is None or heartbeat.state != "ONLINE":
            continue
        observed_at = int(heartbeat.payload.get("observed_at_ms") or 0)
        if observed_at <= 0 or observed_at > self.clock_ms() + 30_000:
            continue
        inflight = max(0, int(heartbeat.payload.get("inflight") or 0))
        slots = max(1, int(provider.get("parallel_slots") or 1))
        headroom = max(0, slots - inflight)
        if headroom <= 0:
            continue
        candidates.append((headroom, node_id, provider))
    if not candidates:
        raise RuntimeError(
            "no approved surviving node has a fresh heartbeat and delegation headroom"
        )
    headroom, node_id, provider = sorted(
        candidates,
        key=lambda item: (-item[0], item[1]),
    )[0]
    record = self.store.upsert_fenced(
        kind="delegation",
        logical_id=task_id,
        state="PLANNED",
        owner=owner,
        epoch=epoch,
        ttl_seconds=control._bounded_ttl(body.get("ttl_seconds"), 120),
        payload={
            "target_node_id": node_id,
            "runtime_instance_id": provider.get("runtime_instance_id"),
            "provider": provider.get("name"),
            "headroom": headroom,
            "required_capabilities": sorted(required),
            "heartbeat_observed_at_ms": int(
                self.store.get("heartbeat", node_id).payload["observed_at_ms"]
            ),
            "projection_generation": projection.get("generation"),
            "projection_checksum": projection.get("checksum"),
        },
    )
    return record.as_dict()


def install_degraded_control_hardening() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    control._is_local_provider = _private_local_provider
    control.DegradedControlPlaneRuntime.plan_delegation = _fresh_heartbeat_plan
    _INSTALLED = True
