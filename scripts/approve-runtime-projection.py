#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import pathlib
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import yaml
from neo4j import GraphDatabase


_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
_UNKNOWN = {"", "unknown", "unresolved", "none", "null", "replace-me"}
_ALLOWED_TRANSPORTS = {"lan", "tailscale", "loopback", "host_gateway", "local_dns"}
_ALLOWED_RUNTIME_KINDS = {
    "lmstudio",
    "lm_studio",
    "llama_cpp",
    "llamacpp",
    "vllm",
    "sglang",
    "openai_compatible",
}


@dataclass(frozen=True)
class ValidatedManifest:
    payload: dict[str, Any]
    checksum: str


def _known(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and text not in _UNKNOWN and not text.startswith("replace-")


def _private_url(value: Any) -> bool:
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
        or address in _TAILSCALE
    )


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def validate_manifest(payload: dict[str, Any]) -> ValidatedManifest:
    failures: list[str] = []
    if int(payload.get("schema_version") or 0) != 1:
        failures.append("schema_version must equal 1")
    try:
        generation = int(payload.get("generation") or 0)
        expected_generation = int(payload.get("expected_current_generation") or 0)
        ttl_seconds = int(payload.get("ttl_seconds") or 0)
    except (TypeError, ValueError):
        generation = 0
        expected_generation = -1
        ttl_seconds = 0
        failures.append("generation, expected_current_generation, and ttl_seconds must be integers")
    if generation <= 0:
        failures.append("generation must be positive")
    if expected_generation < 0:
        failures.append("expected_current_generation must be zero or positive")
    if generation != expected_generation + 1:
        failures.append("generation must be exactly expected_current_generation + 1")
    if ttl_seconds < 30 or ttl_seconds > 900:
        failures.append("ttl_seconds must be between 30 and 900")
    for field in ("revision", "approved_by", "approval_id"):
        if not _known(payload.get(field)):
            failures.append(f"{field} must contain operator-reviewed evidence")

    require_dual_path = bool(payload.get("require_lan_and_tailscale", True))
    runtimes = payload.get("runtimes")
    if not isinstance(runtimes, list) or not runtimes:
        failures.append("runtimes must contain at least one runtime")
        runtimes = []

    runtime_ids: set[str] = set()
    model_instance_ids: set[str] = set()
    for index, runtime in enumerate(runtimes):
        label = f"runtimes[{index}]"
        if not isinstance(runtime, dict):
            failures.append(f"{label} must be a mapping")
            continue
        runtime_id = str(runtime.get("runtime_instance_id") or "").strip()
        node_id = str(runtime.get("node_id") or "").strip()
        runtime_kind = str(runtime.get("runtime_kind") or "").strip().lower()
        runtime_version = runtime.get("runtime_version")
        process_id = runtime.get("process_id")
        for field, value in (
            ("runtime_instance_id", runtime_id),
            ("node_id", node_id),
            ("runtime_kind", runtime_kind),
            ("runtime_version", runtime_version),
            ("process_id", process_id),
        ):
            if not _known(value):
                failures.append(f"{label}.{field} must be resolved")
        if runtime_kind not in _ALLOWED_RUNTIME_KINDS:
            failures.append(f"{label}.runtime_kind is unsupported: {runtime_kind!r}")
        if runtime_id in runtime_ids:
            failures.append(f"duplicate runtime_instance_id {runtime_id!r}")
        runtime_ids.add(runtime_id)

        capacity = runtime.get("capacity")
        if not isinstance(capacity, dict):
            failures.append(f"{label}.capacity must be a mapping")
            capacity = {}
        try:
            slots = int(capacity.get("parallel_slots") or 0)
            queue_limit = int(capacity.get("queue_limit") or 0)
            queue_timeout = float(capacity.get("queue_timeout_seconds") or 0)
        except (TypeError, ValueError):
            slots = 0
            queue_limit = -1
            queue_timeout = -1
        if slots <= 0:
            failures.append(f"{label}.capacity.parallel_slots must be positive")
        if queue_limit < 0:
            failures.append(f"{label}.capacity.queue_limit must be zero or positive")
        if queue_timeout < 0:
            failures.append(f"{label}.capacity.queue_timeout_seconds must be zero or positive")
        if not _known(capacity.get("evidence_ref")):
            failures.append(f"{label}.capacity.evidence_ref is required")

        paths = runtime.get("access_paths")
        if not isinstance(paths, list) or not paths:
            failures.append(f"{label}.access_paths must contain approved paths")
            paths = []
        seen_urls: set[str] = set()
        transports: set[str] = set()
        preferences: set[int] = set()
        for path_index, path in enumerate(paths):
            path_label = f"{label}.access_paths[{path_index}]"
            if not isinstance(path, dict):
                failures.append(f"{path_label} must be a mapping")
                continue
            base_url = str(path.get("base_url") or "").strip().rstrip("/")
            transport = str(path.get("transport") or "").strip().lower()
            try:
                preference = int(path.get("preference"))
            except (TypeError, ValueError):
                preference = -1
            if not _private_url(base_url):
                failures.append(f"{path_label}.base_url must be private and valid")
            if base_url in seen_urls:
                failures.append(f"{path_label}.base_url is duplicated")
            seen_urls.add(base_url)
            if transport not in _ALLOWED_TRANSPORTS:
                failures.append(f"{path_label}.transport is unsupported")
            transports.add(transport)
            if preference < 0 or preference in preferences:
                failures.append(f"{path_label}.preference must be unique and non-negative")
            preferences.add(preference)
            if not _known(path.get("evidence_ref")):
                failures.append(f"{path_label}.evidence_ref is required")
        if require_dual_path and not {"lan", "tailscale"}.issubset(transports):
            failures.append(f"{label} must have both LAN and Tailscale approved paths")
        if paths:
            ordered = sorted(paths, key=lambda item: int(item.get("preference", 999999)))
            if str(ordered[0].get("transport") or "").lower() != "lan":
                failures.append(f"{label} must prefer LAN before fallback transports")

        models = runtime.get("models")
        if not isinstance(models, list) or not models:
            failures.append(f"{label}.models must contain at least one loaded model")
            models = []
        for model_index, model in enumerate(models):
            model_label = f"{label}.models[{model_index}]"
            if not isinstance(model, dict):
                failures.append(f"{model_label} must be a mapping")
                continue
            model_instance_id = str(model.get("model_instance_id") or "").strip()
            for field in (
                "model_instance_id",
                "model_key",
                "provider_model",
                "artifact_fingerprint",
                "quantization",
                "evidence_ref",
            ):
                if not _known(model.get(field)):
                    failures.append(f"{model_label}.{field} must be resolved")
            if model_instance_id in model_instance_ids:
                failures.append(f"duplicate model_instance_id {model_instance_id!r}")
            model_instance_ids.add(model_instance_id)
            try:
                context_length = int(model.get("context_length") or 0)
            except (TypeError, ValueError):
                context_length = 0
            if context_length <= 0:
                failures.append(f"{model_label}.context_length must be positive")
            capabilities = model.get("capabilities")
            if not isinstance(capabilities, list) or not capabilities:
                failures.append(f"{model_label}.capabilities must be a non-empty list")
            elif "local_only" not in {str(item) for item in capabilities}:
                failures.append(f"{model_label}.capabilities must include local_only")

    if failures:
        raise ValueError("; ".join(sorted(set(failures))))
    normalized = json.loads(json.dumps(payload))
    return ValidatedManifest(
        payload=normalized,
        checksum=hashlib.sha256(_canonical(normalized)).hexdigest(),
    )


def _apply_transaction(tx: Any, manifest: ValidatedManifest, now_ms: int) -> dict[str, Any]:
    payload = manifest.payload
    generation = int(payload["generation"])
    expected = int(payload["expected_current_generation"])
    expires_at_ts = now_ms + int(payload["ttl_seconds"]) * 1000
    state = tx.run(
        """
        MERGE (s:FleetProjectionState {name:'canonical'})
        ON CREATE SET s.generation=0, s.status='uninitialized',
                      s.created_at=datetime(), s.created_at_ts=timestamp()
        RETURN coalesce(s.generation, 0) AS generation
        """
    ).single()
    current = int(state["generation"] if state else 0)
    if current != expected:
        raise RuntimeError(
            f"generation compare-and-swap failed: expected {expected}, current {current}"
        )

    tx.run(
        """
        MATCH (r:RuntimeInstance) SET r.admitted=false
        WITH count(r) AS ignored
        MATCH (m:LoadedModelInstance) SET m.admitted=false
        WITH count(m) AS ignored
        MATCH (a:AccessPath) SET a.approved=false
        WITH count(a) AS ignored
        MATCH (c:CapacityObservation) SET c.approved=false
        RETURN count(c) AS retired
        """
    ).consume()

    runtime_count = 0
    model_count = 0
    path_count = 0
    for runtime in payload["runtimes"]:
        runtime_count += 1
        runtime_id = str(runtime["runtime_instance_id"])
        tx.run(
            """
            MERGE (r:RuntimeInstance {runtime_instance_id:$runtime_instance_id})
            ON CREATE SET r.created_at=datetime(), r.created_at_ts=timestamp()
            SET r.node_id=$node_id,
                r.runtime_kind=$runtime_kind,
                r.runtime_version=$runtime_version,
                r.headless=$headless,
                r.process_id=$process_id,
                r.status='online',
                r.admitted=true,
                r.projection_generation=$generation,
                r.approved_by=$approved_by,
                r.approval_id=$approval_id,
                r.observed_at_ts=$now_ms,
                r.expires_at_ts=$expires_at_ts,
                r.updated_at=datetime(), r.updated_at_ts=timestamp()
            """,
            runtime_instance_id=runtime_id,
            node_id=runtime["node_id"],
            runtime_kind=runtime["runtime_kind"],
            runtime_version=str(runtime["runtime_version"]),
            headless=runtime.get("headless"),
            process_id=str(runtime["process_id"]),
            generation=generation,
            approved_by=payload["approved_by"],
            approval_id=payload["approval_id"],
            now_ms=now_ms,
            expires_at_ts=expires_at_ts,
        ).consume()

        capacity = runtime["capacity"]
        capacity_id = f"capacity:{generation}:{runtime_id}"
        tx.run(
            """
            MERGE (c:CapacityObservation {capacity_observation_id:$capacity_id})
            ON CREATE SET c.created_at=datetime(), c.created_at_ts=timestamp()
            SET c.runtime_instance_id=$runtime_instance_id,
                c.parallel_slots=$parallel_slots,
                c.queue_limit=$queue_limit,
                c.queue_timeout_seconds=$queue_timeout_seconds,
                c.evidence_ref=$evidence_ref,
                c.approved=true,
                c.projection_generation=$generation,
                c.approved_by=$approved_by,
                c.approval_id=$approval_id,
                c.observed_at_ts=$now_ms,
                c.expires_at_ts=$expires_at_ts,
                c.updated_at=datetime(), c.updated_at_ts=timestamp()
            WITH c
            MATCH (r:RuntimeInstance {runtime_instance_id:$runtime_instance_id})
            MERGE (r)-[:HAS_CAPACITY_OBSERVATION]->(c)
            """,
            capacity_id=capacity_id,
            runtime_instance_id=runtime_id,
            parallel_slots=int(capacity["parallel_slots"]),
            queue_limit=int(capacity["queue_limit"]),
            queue_timeout_seconds=float(capacity["queue_timeout_seconds"]),
            evidence_ref=capacity["evidence_ref"],
            generation=generation,
            approved_by=payload["approved_by"],
            approval_id=payload["approval_id"],
            now_ms=now_ms,
            expires_at_ts=expires_at_ts,
        ).consume()

        for path in runtime["access_paths"]:
            path_count += 1
            normalized_url = str(path["base_url"]).rstrip("/")
            path_id = hashlib.sha256(
                f"{runtime_id}|{path['transport']}|{normalized_url}".encode()
            ).hexdigest()
            tx.run(
                """
                MERGE (a:AccessPath {access_path_id:$access_path_id})
                ON CREATE SET a.created_at=datetime(), a.created_at_ts=timestamp()
                SET a.runtime_instance_id=$runtime_instance_id,
                    a.base_url=$base_url,
                    a.transport=$transport,
                    a.preference=$preference,
                    a.evidence_ref=$evidence_ref,
                    a.approved=true,
                    a.projection_generation=$generation,
                    a.approved_by=$approved_by,
                    a.approval_id=$approval_id,
                    a.observed_at_ts=$now_ms,
                    a.expires_at_ts=$expires_at_ts,
                    a.updated_at=datetime(), a.updated_at_ts=timestamp()
                WITH a
                MATCH (r:RuntimeInstance {runtime_instance_id:$runtime_instance_id})
                MERGE (a)-[:REACHES]->(r)
                """,
                access_path_id=path_id,
                runtime_instance_id=runtime_id,
                base_url=normalized_url,
                transport=path["transport"],
                preference=int(path["preference"]),
                evidence_ref=path["evidence_ref"],
                generation=generation,
                approved_by=payload["approved_by"],
                approval_id=payload["approval_id"],
                now_ms=now_ms,
                expires_at_ts=expires_at_ts,
            ).consume()

        for model in runtime["models"]:
            model_count += 1
            artifact_id = str(model["artifact_fingerprint"])
            tx.run(
                """
                MERGE (artifact:ModelArtifact {artifact_fingerprint:$artifact_fingerprint})
                ON CREATE SET artifact.created_at=datetime(),
                              artifact.created_at_ts=timestamp()
                SET artifact.quantization=$quantization,
                    artifact.context_length=$context_length,
                    artifact.capabilities_json=$capabilities_json,
                    artifact.updated_at=datetime(),
                    artifact.updated_at_ts=timestamp()
                WITH artifact
                MERGE (m:LoadedModelInstance {model_instance_id:$model_instance_id})
                ON CREATE SET m.created_at=datetime(), m.created_at_ts=timestamp()
                SET m.model_key=$model_key,
                    m.provider_model=$provider_model,
                    m.artifact_fingerprint=$artifact_fingerprint,
                    m.quantization=$quantization,
                    m.context_length=$context_length,
                    m.capabilities_json=$capabilities_json,
                    m.evidence_ref=$evidence_ref,
                    m.admitted=true,
                    m.projection_generation=$generation,
                    m.approved_by=$approved_by,
                    m.approval_id=$approval_id,
                    m.observed_at_ts=$now_ms,
                    m.expires_at_ts=$expires_at_ts,
                    m.updated_at=datetime(), m.updated_at_ts=timestamp()
                WITH artifact, m
                MATCH (r:RuntimeInstance {runtime_instance_id:$runtime_instance_id})
                MERGE (r)-[:SERVES]->(m)
                MERGE (m)-[:INSTANCE_OF]->(artifact)
                """,
                runtime_instance_id=runtime_id,
                model_instance_id=model["model_instance_id"],
                model_key=model["model_key"],
                provider_model=model["provider_model"],
                artifact_fingerprint=artifact_id,
                quantization=model["quantization"],
                context_length=int(model["context_length"]),
                capabilities_json=json.dumps(sorted(set(model["capabilities"]))),
                evidence_ref=model["evidence_ref"],
                generation=generation,
                approved_by=payload["approved_by"],
                approval_id=payload["approval_id"],
                now_ms=now_ms,
                expires_at_ts=expires_at_ts,
            ).consume()

    tx.run(
        """
        MATCH (s:FleetProjectionState {name:'canonical'})
        SET s.generation=$generation,
            s.revision=$revision,
            s.status='approved',
            s.approved_by=$approved_by,
            s.approval_id=$approval_id,
            s.manifest_checksum=$manifest_checksum,
            s.expires_at_ts=$expires_at_ts,
            s.updated_at=datetime(), s.updated_at_ts=timestamp()
        """,
        generation=generation,
        revision=payload["revision"],
        approved_by=payload["approved_by"],
        approval_id=payload["approval_id"],
        manifest_checksum=manifest.checksum,
        expires_at_ts=expires_at_ts,
    ).consume()
    return {
        "generation": generation,
        "revision": payload["revision"],
        "manifest_checksum": manifest.checksum,
        "runtime_count": runtime_count,
        "model_count": model_count,
        "access_path_count": path_count,
        "expires_at_ts": expires_at_ts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and atomically approve one AssistX canonical runtime projection. "
            "Dry-run is the default; --apply is required for any Neo4j write."
        )
    )
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--evidence-output", type=pathlib.Path)
    args = parser.parse_args()

    try:
        payload = yaml.safe_load(args.manifest.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be a mapping")
        manifest = validate_manifest(payload)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"RUNTIME_PROJECTION_APPROVAL: BLOCKED {exc}", file=sys.stderr)
        return 2

    summary: dict[str, Any] = {
        "status": "validated",
        "mode": "dry-run",
        "manifest": str(args.manifest),
        "manifest_checksum": manifest.checksum,
        "generation": manifest.payload["generation"],
        "revision": manifest.payload["revision"],
        "runtime_count": len(manifest.payload["runtimes"]),
    }
    if args.apply:
        uri = os.getenv("NEO4J_URI", "").strip()
        user = os.getenv("NEO4J_USER", "neo4j").strip()
        password = os.getenv("NEO4J_PASSWORD", "").strip()
        database = os.getenv("NEO4J_DATABASE", "neo4j").strip()
        if not uri or not password:
            print(
                "RUNTIME_PROJECTION_APPROVAL: BLOCKED NEO4J_URI and NEO4J_PASSWORD are required",
                file=sys.stderr,
            )
            return 2
        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session(database=database) as session:
                applied = session.execute_write(
                    _apply_transaction,
                    manifest,
                    int(time.time() * 1000),
                )
        except Exception as exc:
            print(
                f"RUNTIME_PROJECTION_APPROVAL: BLOCKED {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
        finally:
            driver.close()
        summary.update(applied)
        summary["status"] = "approved"
        summary["mode"] = "apply"

    summary["evidence_sha256"] = hashlib.sha256(_canonical(summary)).hexdigest()
    output = args.evidence_output or args.manifest.with_suffix(".approval-evidence.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "RUNTIME_PROJECTION_APPROVAL: "
        f"{'PASS' if args.apply else 'DRY_RUN_PASS'} "
        f"generation={summary['generation']} checksum={manifest.checksum}"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
