#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_RUNTIME_KIND_ALIASES = {
    "lmstudio": "lmstudio",
    "lm_studio": "lmstudio",
    "lm studio": "lmstudio",
    "llama.cpp": "llama_cpp",
    "llama_cpp": "llama_cpp",
    "llamacpp": "llama_cpp",
    "vllm": "vllm",
    "sglang": "sglang",
    "openai_compatible": "openai_compatible",
    "openai-compatible": "openai_compatible",
}

_REQUIRED_ADMISSION_EVIDENCE = [
    "physical_runtime_identity",
    "runtime_version_and_process_id",
    "model_artifact_fingerprint",
    "model_quantization_and_context",
    "capacity_observation",
    "approved_access_paths",
    "lan_preference_and_tailscale_fallback",
    "runtime_canary_soak",
    "runtime_canary_rollback",
    "operator_approval",
]


def _kind(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return _RUNTIME_KIND_ALIASES.get(raw, raw or "unknown")


def _port(value: Any) -> int | None:
    try:
        parsed = urlparse(str(value or "").strip())
        if parsed.port is not None:
            return int(parsed.port)
        if parsed.scheme == "https":
            return 443
        if parsed.scheme == "http":
            return 80
    except (ValueError, TypeError):
        return None
    return None


def _node_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _provider_ports(provider: dict[str, Any]) -> set[int]:
    urls: list[Any] = []
    if provider.get("base_url"):
        urls.append(provider["base_url"])
    urls.extend(provider.get("access_urls") or [])
    return {port for url in urls if (port := _port(url)) is not None}


def _provider_model_ids(provider: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for model in provider.get("models") or []:
        if not isinstance(model, dict):
            continue
        for key in ("alias", "provider_model"):
            value = str(model.get(key) or "").strip()
            if value:
                out.add(value.casefold())
    return out


def _projection_index(projection: dict[str, Any]) -> dict[tuple[str, int, str], list[dict[str, Any]]]:
    index: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for provider in projection.get("providers") or []:
        if not isinstance(provider, dict) or provider.get("enabled") is False:
            continue
        node = _node_id(provider.get("node_id") or provider.get("hostname"))
        runtime_kind = _kind(provider.get("runtime_kind") or provider.get("type"))
        if not node or runtime_kind == "unknown":
            continue
        for port in _provider_ports(provider):
            index.setdefault((node, port, runtime_kind), []).append(provider)
    return index


def reconcile(
    nodes_payload: dict[str, Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    """Compare fresh runtime observations with the signed admitted projection.

    This is deliberately non-mutating. Runtime observation is discovery
    evidence, never admission authority.
    """

    index = _projection_index(projection)
    items: list[dict[str, Any]] = []
    observed_keys: set[tuple[str, int, str]] = set()

    for node_report in nodes_payload.get("nodes") or []:
        if not isinstance(node_report, dict):
            continue
        node = _node_id(
            node_report.get("hostname")
            or node_report.get("host_name")
            or node_report.get("node_id")
        )
        if not node:
            continue
        for observation in node_report.get("runtimes") or []:
            if not isinstance(observation, dict):
                continue
            if observation.get("observation_schema") != "fleet-runtime-observation.v1":
                continue
            if observation.get("admitted") is True:
                # Reporter/router contracts force this false. Refuse to treat an
                # observation claiming admission as trustworthy evidence.
                continue
            runtime_kind = _kind(observation.get("runtime_kind"))
            port = _port(observation.get("base_url"))
            if port is None:
                continue
            key = (node, port, runtime_kind)
            observed_keys.add(key)
            matches = index.get(key, [])
            observed_models = sorted(
                {
                    str(value).strip()
                    for value in (observation.get("models") or [])
                    if str(value).strip()
                },
                key=str.casefold,
            )

            status: str
            action: str
            missing_models: list[str] = []
            matched_runtime_ids: list[str] = []
            if len(matches) == 1:
                provider = matches[0]
                matched_runtime_ids = [
                    str(provider.get("runtime_instance_id") or provider.get("name") or "")
                ]
                projected_models = _provider_model_ids(provider)
                missing_models = [
                    model
                    for model in observed_models
                    if model.casefold() not in projected_models
                ]
                if missing_models:
                    status = "model_drift"
                    action = "collect_model_identity_evidence"
                else:
                    status = "projected"
                    action = "none"
            elif len(matches) > 1:
                status = "ambiguous_projection_match"
                action = "review_runtime_identity"
                matched_runtime_ids = sorted(
                    {
                        str(
                            provider.get("runtime_instance_id")
                            or provider.get("name")
                            or ""
                        )
                        for provider in matches
                    }
                )
            else:
                status = "unprojected_runtime"
                action = "collect_admission_evidence"

            items.append(
                {
                    "node_id": node,
                    "runtime_observation_id": str(
                        observation.get("runtime_observation_id") or ""
                    ),
                    "runtime_kind": runtime_kind,
                    "port": port,
                    "observed_models": observed_models,
                    "ready": bool(observation.get("ready")),
                    "status": status,
                    "action": action,
                    "matched_runtime_ids": matched_runtime_ids,
                    "missing_models": missing_models,
                    "required_admission_evidence": (
                        list(_REQUIRED_ADMISSION_EVIDENCE)
                        if status == "unprojected_runtime"
                        else []
                    ),
                }
            )

    projected_without_observation: list[dict[str, Any]] = []
    for key, providers in sorted(index.items()):
        if key in observed_keys:
            continue
        node, port, runtime_kind = key
        projected_without_observation.append(
            {
                "node_id": node,
                "port": port,
                "runtime_kind": runtime_kind,
                "runtime_instance_ids": sorted(
                    {
                        str(
                            provider.get("runtime_instance_id")
                            or provider.get("name")
                            or ""
                        )
                        for provider in providers
                    }
                ),
            }
        )

    items.sort(
        key=lambda item: (
            str(item["node_id"]),
            int(item["port"]),
            str(item["runtime_kind"]),
        )
    )
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    return {
        "schema_version": "assistx.runtime-observation-reconciliation.v1",
        "projection_generation": projection.get("generation"),
        "projection_revision": projection.get("revision"),
        "summary": {
            "observed_runtime_count": len(items),
            "projected": counts.get("projected", 0),
            "unprojected_runtime": counts.get("unprojected_runtime", 0),
            "model_drift": counts.get("model_drift", 0),
            "ambiguous_projection_match": counts.get(
                "ambiguous_projection_match",
                0,
            ),
            "projected_without_observation": len(
                projected_without_observation
            ),
        },
        "items": items,
        "projected_without_observation": projected_without_observation,
        "mutating": False,
        "admission_authority": False,
    }


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Auto-Router fleet runtime observations with one AssistX "
            "signed runtime projection. This command is read-only and never admits "
            "a runtime."
        )
    )
    parser.add_argument("--nodes", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    result = reconcile(_load(args.nodes), _load(args.projection))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")

    summary = result["summary"]
    print(
        "RUNTIME_OBSERVATION_RECONCILIATION: "
        f"observed={summary['observed_runtime_count']} "
        f"projected={summary['projected']} "
        f"unprojected={summary['unprojected_runtime']} "
        f"model_drift={summary['model_drift']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
