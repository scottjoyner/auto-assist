"""Read-only, operator-only embedding admission preview for Fleet Model Link.

This endpoint does not issue embedding requests, promote models, or expose nodes.
"""
from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, Depends

from .fleet_link_canary import _MODEL_RE, _json_get, _make_client, _pinned_sidecar_url
from .runtime_projection import projection_checksum

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{80,90}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_KIND = "embedding"
_SCHEMA = "assistx-fleet-link-embedding-preview.v1"
_CAPS = {"embedding", "embeddings"}


def _fresh_signed_projection(document: Any) -> bool:
    """Only internally issued v2 Ed25519 projections are even considered.

    This is a structural precondition: the projection_factory is the trusted
    in-process Ed25519 issuer; this function is NOT a signature verifier.
    """
    if not isinstance(document, dict):
        return False
    now = int(time.time() * 1000)
    issued = document.get("generated_at_ms")
    expiry = document.get("expires_at_ms")
    return (
        document.get("source") == "assistx"
        and document.get("schema_version") == "2"
        and type(document.get("generation")) is int
        and document["generation"] > 0
        and type(issued) is int
        and now-900_000 <= issued <= now+5_000
        and type(expiry) is int
        and issued < expiry <= issued+900_000
        and expiry > now
        and document.get("signature_algorithm") == "Ed25519"
        and isinstance(document.get("signature_key_id"), str)
        and bool(document["signature_key_id"].strip())
        and isinstance(document.get("signature"), str)
        and bool(_SIGNATURE.fullmatch(document["signature"]))
        and isinstance(document.get("checksum"), str)
        and bool(_HEX64.fullmatch(document["checksum"]))
        and document["checksum"] == projection_checksum(document)
        and isinstance(document.get("providers"), list)
        and len(document["providers"]) <= 64
    )


def _admitted_embedding_routes(document: Any, url: str) -> dict[str, str]:
    """Build exact route -> approved artifact SHA map; ambiguities quarantined.

    No HMAC mobile model handle or artifact hashes are returned to the caller.
    """
    if not _fresh_signed_projection(document):
        return {}
    runtime_id = os.getenv("ASSISTX_FLEET_LINK_RUNTIME_ID", "").strip()
    matches: dict[str, str] = {}
    ambiguous: set[str] = set()
    for provider in document["providers"]:
        if not isinstance(provider, dict) or not (
            provider.get("enabled") is True
            and provider.get("runtime_instance_id") == runtime_id
            and provider.get("runtime_kind") == "openai_compatible"
            and provider.get("type") == "openai_compatible"
            and provider.get("base_url") == url + "/v1"
            and provider.get("access_urls") == [url + "/v1"]
            and isinstance(provider.get("node_id"), str)
            and bool(provider["node_id"].strip())
        ):
            continue
        models = provider.get("models")
        if not isinstance(models, list) or len(models) > 64:
            continue
        for m in models:
            if not isinstance(m, dict) or m.get("enabled") is False:
                continue
            route = m.get("provider_model")
            artifact = m.get("artifact_fingerprint")
            capabilities = m.get("capabilities")
            if not (
                isinstance(route, str)
                and bool(_MODEL_RE.fullmatch(route))
                and not route.startswith("auto/")
                and isinstance(artifact, str)
                and bool(_SHA256.fullmatch(artifact))
                and isinstance(capabilities, list)
                and "local_only" in capabilities
                and _CAPS.intersection(capabilities)
                and "chat" not in capabilities
                and m.get("allow_code_execution") is not True
                and m.get("allow_agent_runtime") is not True
            ):
                continue
            if route in matches:
                # Even identically hashed duplicated approval rows must not be
                # counted twice or accidentally widen the set of authorities.
                ambiguous.add(route)
            matches[route] = artifact
    return {route: digest for route, digest in matches.items() if route not in ambiguous}


def _observed_embedding_routes(status: Any, inventory: Any) -> set[str]:
    """Conservative intersection: the sidecar's two separate read-only views.

    Even a positive result is only observational; the sidecar response is not
    cryptographically authenticated to AssistX by this diagnostic.
    """
    if not isinstance(status, dict) or not isinstance(inventory, dict):
        return set()
    if not (
        status.get("schema") == "fleet-model-link.v1"
        and status.get("catalog_expired") is False
        and type(status.get("generation")) is int
        and status["generation"] > 0
        and isinstance(status.get("node"), str)
        and bool(status["node"])
        and inventory.get("object") == "list"
    ):
        return set()
    status_rows = status.get("models")
    inventory_rows = inventory.get("data")
    if not (
        isinstance(status_rows, list)
        and isinstance(inventory_rows, list)
        and len(status_rows) <= 64
        and len(inventory_rows) <= 64
    ):
        return set()
    status_seen = set()
    status_dup = set()
    candidates = set()
    for row in status_rows:
        if not isinstance(row, dict):
            return set()
        route = row.get("id")
        if not isinstance(route, str) or not _MODEL_RE.fullmatch(route):
            return set()
        if route in status_seen:
            status_dup.add(route)
        status_seen.add(route)
        if (
            row.get("kind") == _KIND
            and row.get("ready_now") is True
            and row.get("status") == "READY_PROVED_RECENTLY"
        ):
            candidates.add(route)
    inventory_seen = set()
    inventory_dup = set()
    listed = set()
    for row in inventory_rows:
        if not isinstance(row, dict):
            return set()
        route = row.get("id")
        if not isinstance(route, str) or not _MODEL_RE.fullmatch(route):
            return set()
        if route in inventory_seen:
            inventory_dup.add(route)
        inventory_seen.add(route)
        if row.get("object") == "model" and row.get("kind") == _KIND:
            listed.add(route)
    return (candidates & listed) - status_dup - inventory_dup


def _empty_preview() -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "mode": "NOT_VERIFIED",
        "admitted_embedding_count": 0,
        "observed_ready_embedding_count": 0,
        "matched_embedding_count": 0,
        "execution_authorized": False,
        "automatic_promotion": False,
        "artifact_identity_independently_proved": False,
        "sidecar_identity_cryptographically_verified": False,
        "catalog_generation_hint": None,
    }


def build_fleet_link_embedding_preview_router(
    auth_dependency: Callable[..., Any],
    projection_factory: Callable[[], dict[str, Any]],
    client_factory: Callable[[], httpx.AsyncClient] = _make_client,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/fleet-link/embeddings",
        tags=["fleet-link-embedding-preview"],
        dependencies=[Depends(auth_dependency)],
    )

    @router.get("/preview")
    async def embedding_preview() -> dict[str, Any]:
        try:
            # Separate feature flag from the chat canary. Opt-in remains required.
            url = _pinned_sidecar_url("ASSISTX_FLEET_LINK_EMBEDDING_PREVIEW")
            projection = projection_factory()
            approved = _admitted_embedding_routes(projection, url)
            # No approved artifact: do not probe even the loopback sidecar.
            if not approved:
                return _empty_preview()
            async with client_factory() as client:
                status = await _json_get(client, url + "/fleet/nodes")
                inventory = await _json_get(client, url + "/fleet/embeddings")
            ready = _observed_embedding_routes(status, inventory)
            return {
                "schema": _SCHEMA,
                "mode": "OPERATOR_OBSERVATION_ONLY",
                "admitted_embedding_count": len(approved),
                "observed_ready_embedding_count": len(ready),
                "matched_embedding_count": len(approved.keys() & ready),
                "execution_authorized": False,
                "automatic_promotion": False,
                "artifact_identity_independently_proved": False,
                "sidecar_identity_cryptographically_verified": False,
                "catalog_generation_hint": status["generation"]
                    if ready and isinstance(status.get("generation"), int) else None,
            }
        except Exception:
            # No internal node routes, model IDs, fingerprints, signatures,
            # credentials, raw exception messages, or sidecar identities leak.
            return _empty_preview()

    return router
