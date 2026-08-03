from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from typing import Any

from . import degraded_control_plane as control
from .recovery_snapshot import private_http_url
from .runtime_projection import projection_checksum, projection_signature

_INSTALLED = False
_ORIGINAL_PUBLISH = control.DegradedControlPlaneRuntime.publish_runtime_projection


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


def _secure_publish(self, document: Mapping[str, Any], *, secret: str) -> dict[str, Any]:
    projection = dict(document)
    if not secret:
        raise ValueError("runtime projection HMAC secret is required")
    expected_checksum = projection_checksum(projection)
    supplied_checksum = str(projection.get("checksum") or "")
    if not hmac.compare_digest(supplied_checksum, expected_checksum):
        raise ValueError("runtime projection checksum mismatch")
    expected_signature = projection_signature(
        int(projection.get("generation") or 0),
        expected_checksum,
        int(projection.get("generated_at_ms") or 0),
        int(projection.get("expires_at_ms") or 0),
        secret,
    )
    if not hmac.compare_digest(
        str(projection.get("signature") or ""),
        expected_signature,
    ):
        raise ValueError("runtime projection signature mismatch")
    return _ORIGINAL_PUBLISH(self, projection, secret=secret)


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
    candidates: list[tuple[int, str, dict[str, Any], Any]] = []
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
        candidates.append((headroom, node_id, provider, heartbeat))
    if not candidates:
        raise RuntimeError(
            "no approved surviving node has a fresh heartbeat and delegation headroom"
        )
    headroom, node_id, provider, heartbeat = sorted(
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
                heartbeat.payload["observed_at_ms"]
            ),
            "projection_generation": projection.get("generation"),
            "projection_checksum": projection.get("checksum"),
        },
    )
    return record.as_dict()


def _key_specific_finalization(self, body: Mapping[str, Any]) -> dict[str, Any]:
    operation_id = control._required_text(body.get("operation_id"), "operation_id")
    payload = {
        "operation_id": operation_id,
        "operation_kind": control._required_text(
            body.get("operation_kind"),
            "operation_kind",
        ),
        "final_state": control._required_text(
            body.get("final_state"),
            "final_state",
        ).upper(),
        "record_checksum": control._required_text(
            body.get("record_checksum"),
            "record_checksum",
        ),
        "epoch": control._required_epoch(body.get("epoch")),
        "evidence": dict(body.get("evidence") or {}),
        "requested_at_ms": self.clock_ms(),
    }
    key = "finalize:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    self.journal.append(
        idempotency_key=key,
        status="PENDING",
        payload=payload,
    )
    if not self.neo_factory or not os.getenv("NEO4J_URI", "").strip():
        return {
            "status": "PENDING_DURABLE_COMMIT",
            "idempotency_key": key,
        }
    replay = self.journal.replay(self._neo4j_commit, limit=1000)
    still_pending = {
        entry.idempotency_key for entry in self.journal.pending()
    }
    return {
        "status": (
            "PENDING_DURABLE_COMMIT"
            if key in still_pending
            else "COMMITTED"
        ),
        "idempotency_key": key,
        "replay": replay,
    }


def install_degraded_control_hardening() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    control._is_local_provider = _private_local_provider
    control.DegradedControlPlaneRuntime.publish_runtime_projection = _secure_publish
    control.DegradedControlPlaneRuntime.plan_delegation = _fresh_heartbeat_plan
    control.DegradedControlPlaneRuntime.submit_finalization = _key_specific_finalization
    _INSTALLED = True
