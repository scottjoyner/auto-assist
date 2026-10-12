"""Synthetic-only, deny-only endpoint writer fence experiment.

Not imported by AssistX runtime. Does not configure a live feature flag or
authorize application/graph changes. Requires fabricated scratch-acceptance-
identities and explicitly true rehearsal attestations. Real endpoint IDs,
ambiguous payloads and unreviewed sources always HOLD.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

SYNTHETIC_PREFIX = "scratch-acceptance-"
_KNOWN_OPERATIONS = frozenset({
    "node_register", "node_heartbeat", "model_register", "model_probe",
    "model_delete", "event_ingest",
})
_KNOWN_WRITER_EVENTS = frozenset({
    "swarm.node.registered", "swarm.node.heartbeat",
    "model.endpoint.discovered",
})
_HAZARDOUS_ID_KEYS = frozenset({"endpoint_id", "model_endpoint_id", "id"})
_MAX_IDS = 16


@dataclass(frozen=True)
class SyntheticFence:
    blocked_ids: frozenset[str]
    issuer_policy_approved: bool
    producer_inventory_verified: bool
    queues_drained: bool
    synthetic_rehearsal: bool
    generation: int


@dataclass(frozen=True)
class Decision:
    status: str
    would_invoke_next_stage: bool = False
    production_release_authorized: bool = False


class FenceHold(ValueError):
    """Fixed-code rejection; never echo IDs or raw payloads."""


def _valid_id(value: Any) -> str:
    if (
        type(value) is not str
        or not value.startswith(SYNTHETIC_PREFIX)
        or len(value) > 180
        or any(c.isspace() for c in value)
    ):
        raise FenceHold("HOLD_NON_SYNTHETIC_ID")
    return value


def _normalize(policy: SyntheticFence) -> frozenset[str]:
    if not isinstance(policy, SyntheticFence):
        raise FenceHold("HOLD_NO_OWNER_POLICY")
    if (
        policy.synthetic_rehearsal is not True
        or policy.issuer_policy_approved is not True
        or policy.producer_inventory_verified is not True
        or policy.queues_drained is not True
        or type(policy.generation) is not int
        or policy.generation <= 0
    ):
        raise FenceHold("HOLD_UNVERIFIED_FENCE_AND_DRAIN")
    if (
        not isinstance(policy.blocked_ids, frozenset)
        or not 1 <= len(policy.blocked_ids) <= _MAX_IDS
    ):
        raise FenceHold("HOLD_INVALID_FENCE_SCOPE")
    return frozenset(_valid_id(x) for x in policy.blocked_ids)


def _required_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FenceHold("HOLD_INVALID_PAYLOAD")
    return value


def _optional_id(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return _valid_id(value)


def _service_ids(payload: Mapping[str, Any], *, path_node_id: str | None) -> set[str]:
    """Mirror legacy node_id/service_type default used in swarm_core."""
    node = _optional_id(path_node_id) or _optional_id(
        payload.get("node_id") or payload.get("hostname")
    )
    if node is None:
        raise FenceHold("HOLD_MISSING_NODE_ID")
    if path_node_id and payload.get("node_id") not in (None, "", path_node_id):
        raise FenceHold("HOLD_CONFLICTING_NODE_ID")
    services = payload.get("services", [])
    if not isinstance(services, list) or len(services) > 200:
        raise FenceHold("HOLD_INVALID_SERVICE_LIST")
    resolved = set()
    for item in services:
        svc = _required_mapping(item)
        service_type = svc.get("service_type") or "service"
        if not isinstance(service_type, str) or not service_type or len(service_type) > 80:
            raise FenceHold("HOLD_INVALID_SERVICE_TYPE")
        identity = svc.get("endpoint_id")
        if identity in (None, ""):
            identity = node + ":" + service_type
        identity = _valid_id(identity)
        other = _optional_id(svc.get("model_endpoint_id"))
        if other is not None and other != identity:
            raise FenceHold("HOLD_CONFLICTING_ENDPOINT_IDS")
        resolved.add(identity)
    return resolved


def _model_id(payload: Mapping[str, Any], *, path_endpoint_id: str | None) -> str:
    id_a = _optional_id(payload.get("model_endpoint_id"))
    id_b = _optional_id(payload.get("endpoint_id"))
    id_path = _optional_id(path_endpoint_id)
    present = {x for x in (id_a, id_b, id_path) if x is not None}
    if len(present) > 1:
        raise FenceHold("HOLD_CONFLICTING_ENDPOINT_IDS")
    if present:
        return next(iter(present))
    if path_endpoint_id is not None:
        raise FenceHold("HOLD_INVALID_ROUTE_ENDPOINT")
    node = _optional_id(payload.get("node_id"))
    base_url = payload.get("base_url")
    if node is None or not isinstance(base_url, str) or not base_url:
        raise FenceHold("HOLD_MISSING_ENDPOINT_ID")
    # Legacy default uses base_url. Only fabricated IDs can pass.
    return _valid_id(node + ":model:" + base_url)


def _referenced_ids(value: Any, *, depth: int = 0) -> set[str]:
    """Conservatively inspect explicit ID fields in synthetic event envelopes."""
    if depth > 7:
        raise FenceHold("HOLD_EVENT_DEPTH")
    if isinstance(value, list):
        if len(value) > 200:
            raise FenceHold("HOLD_EVENT_TOO_LARGE")
        out = set()
        for entry in value:
            out.update(_referenced_ids(entry, depth=depth + 1))
        return out
    if isinstance(value, Mapping):
        if len(value) > 100:
            raise FenceHold("HOLD_EVENT_TOO_LARGE")
        out = set()
        for key, entry in value.items():
            if not isinstance(key, str):
                raise FenceHold("HOLD_INVALID_EVENT_KEY")
            if key in _HAZARDOUS_ID_KEYS and entry not in (None, ""):
                out.add(_valid_id(entry))
            if isinstance(entry, (Mapping, list)):
                out.update(_referenced_ids(entry, depth=depth + 1))
        return out
    return set()


def check(
    operation: str,
    payload: Mapping[str, Any],
    policy: SyntheticFence,
    *,
    route_node_id: str | None = None,
    route_endpoint_id: str | None = None,
) -> Decision:
    """Pure dry-run: never invokes a DB, API, filesystem or downstream handler."""
    try:
        blocked = _normalize(policy)
        if operation not in _KNOWN_OPERATIONS:
            raise FenceHold("HOLD_UNKNOWN_INGRESS_PATH")
        data = _required_mapping(payload)
        references: set[str] = set()
        if operation in ("node_register", "node_heartbeat"):
            references.update(_service_ids(data, path_node_id=route_node_id))
        elif operation in ("model_register", "model_probe", "model_delete"):
            references.add(_model_id(data, path_endpoint_id=route_endpoint_id))
        else:
            event_type = data.get("event_type")
            if not isinstance(event_type, str):
                raise FenceHold("HOLD_INVALID_EVENT_TYPE")
            references.update(_referenced_ids(data))
            if event_type in ("swarm.node.registered", "swarm.node.heartbeat"):
                references.update(_service_ids(
                    _required_mapping(data.get("payload")),
                    path_node_id=data.get("node_id"),
                ))
            elif event_type == "model.endpoint.discovered":
                references.add(_model_id(_required_mapping(data.get("payload")),
                                         path_endpoint_id=None))
            else:
                if references & blocked:
                    return Decision("DENY_STALE_REFERENCE_IN_OTHER_EVENT")
                return Decision("ALLOW_UNRELATED_SYNTHETIC_EVENT_REVIEW_ONLY", True)
        if references & blocked:
            return Decision("DENY_SYNTHETIC_FENCED_ENDPOINT")
        return Decision("ALLOW_UNRELATED_SYNTHETIC_OPERATION_REVIEW_ONLY", True)
    except FenceHold as err:
        return Decision(str(err))


def guarded_synthetic_call(
    operation: str,
    payload: Mapping[str, Any],
    policy: SyntheticFence,
    downstream: Callable[[], Any],
    *,
    route_node_id: str | None = None,
    route_endpoint_id: str | None = None,
) -> Decision:
    """Mock-test only. The callback is never invoked for DENY/HOLD."""
    decision = check(operation, payload, policy,
                     route_node_id=route_node_id,
                     route_endpoint_id=route_endpoint_id)
    if decision.would_invoke_next_stage:
        downstream()
    return decision
