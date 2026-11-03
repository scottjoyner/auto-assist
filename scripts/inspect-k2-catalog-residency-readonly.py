#!/usr/bin/env python3
"""LM Studio downloaded vs resident K2 model catalogs — READ ONLY.

A healthy HTTP service or downloaded GGUF is not a running model. This
inspector uses only fixed bounded GETs and can NEVER authorize dispatch.
It does not load, unload, infer, reconfigure or restart models/services.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SCHEMA = "assistx.k2-model-catalog-residency-readonly.v1"
NATIVE_URL = "http://127.0.0.1:1234/api/v1/models"
COMPAT_PATHS = {
    "loopback": "http://127.0.0.1:1234/v1/models",
    "lan": "http://192.168.1.237:1234/v1/models",
    "tailscale": "http://100.64.43.123:1234/v1/models",
}
EXPECTED_MODEL_ID = "k2-horizon-0.9b"
K2_ARCHITECTURE = "k2-horizon"
MAX_BYTES = 262_144
MAX_MODELS = 128
MAX_INSTANCES = 64
KEY_LIMIT = 1024


class CatalogHold(ValueError):
    """Static, non-sensitive refusal. Never expose remote JSON contents."""


def deny(reason: str) -> None:
    raise CatalogHold(reason)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        deny("redirected_model_catalog_forbidden")


def fetch_json(url: str) -> Any:
    if url not in {NATIVE_URL, *COMPAT_PATHS.values()}:
        deny("unapproved_catalog_url")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(
        Request(url, method="GET", headers={"Accept": "application/json"}),
        timeout=4,
    ) as response:
        if response.status != 200:
            deny("catalog_http_not_200")
        raw = response.read(MAX_BYTES + 1)
    if not 0 < len(raw) <= MAX_BYTES:
        deny("catalog_size_invalid")

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict:
        data: dict = {}
        for key, value in pairs:
            if key in data:
                deny("duplicate_catalog_json_field")
            data[key] = value
        return data

    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=reject_duplicate,
            parse_constant=lambda _: deny("nonfinite_catalog_json"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogHold("catalog_json_invalid") from exc


def model_id(text: Any) -> str:
    if not isinstance(text, str) or not 0 < len(text) <= KEY_LIMIT:
        deny("invalid_catalog_model_identifier")
    if re.search(r"[\x00-\x1f\x7f]", text):
        deny("invalid_catalog_model_identifier")
    return text


def native_summary(obj: Any) -> dict:
    if not isinstance(obj, dict) or not isinstance(obj.get("models"), list):
        deny("native_catalog_schema_invalid")
    items = obj["models"]
    if len(items) > MAX_MODELS:
        deny("native_catalog_too_many_models")
    names: set[str] = set()
    instance_ids: set[str] = set()
    report = {
        "available_model_entries": 0,
        "available_llm_entries": 0,
        "available_embedding_entries": 0,
        "available_k2_family_entries": 0,
        "loaded_instance_count": 0,
        "loaded_k2_instance_count": 0,
    }
    for entry in items:
        if not isinstance(entry, dict):
            deny("native_model_entry_invalid")
        key = model_id(entry.get("key"))
        if key in names:
            deny("native_duplicate_model_key")
        names.add(key)
        t = entry.get("type")
        if t not in ("llm", "embedding", "decision"):
            deny("native_model_type_invalid")
        instances = entry.get("loaded_instances")
        if not isinstance(instances, list) or len(instances) > MAX_INSTANCES:
            deny("native_loaded_instances_invalid")
        report["available_model_entries"] += 1
        if t == "llm":
            report["available_llm_entries"] += 1
        elif t == "embedding":
            report["available_embedding_entries"] += 1
        k2 = t == "llm" and entry.get("architecture") == K2_ARCHITECTURE
        if k2:
            report["available_k2_family_entries"] += 1
        for instance in instances:
            if not isinstance(instance, dict):
                deny("native_instance_schema_invalid")
            iid = model_id(instance.get("id"))
            if iid in instance_ids:
                deny("native_duplicate_loaded_instance")
            instance_ids.add(iid)
            report["loaded_instance_count"] += 1
            if k2:
                report["loaded_k2_instance_count"] += 1
    return report


def compat_summary(obj: Any) -> dict:
    if not isinstance(obj, dict) or not isinstance(obj.get("data"), list):
        deny("openai_catalog_schema_invalid")
    rows = obj["data"]
    if len(rows) > MAX_MODELS:
        deny("openai_catalog_too_many_models")
    ids = set()
    for item in rows:
        if not isinstance(item, dict):
            deny("openai_model_entry_invalid")
        iid = model_id(item.get("id"))
        if iid in ids:
            deny("openai_duplicate_model_id")
        ids.add(iid)
    return {
        "loaded_model_ids": len(ids),
        "expected_k2_model_id_present": EXPECTED_MODEL_ID in ids,
    }


def collect(*, fetch: Callable[[str], Any] = fetch_json) -> dict:
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "node": "x1-370",
        "observed_utc": datetime.now(UTC).isoformat(),
        "mode": "read_only_no_model_load",
        "native": None,
        "private_paths": {},
        "all_catalogs_reachable": False,
        "status": "CATALOG_UNAVAILABLE_HOLD",
        "api_reachable_is_not_inference_ready": True,
        "installed_artifact_is_not_running_model": True,
        "physical_model_residency_verified": False,
        "actual_generation_tokens_measured": False,
        "independent_witness_verified": False,
        "canonical_operator_approval_verified": False,
        "can_dispatch": False,
        "decision": "HOLD",
    }
    try:
        result["native"] = native_summary(fetch(NATIVE_URL))
    except (CatalogHold, OSError, ValueError, TimeoutError):
        result["native"] = {"error_code": "NATIVE_CATALOG_UNVERIFIED"}
    for name, url in COMPAT_PATHS.items():
        try:
            result["private_paths"][name] = compat_summary(fetch(url))
        except (CatalogHold, OSError, ValueError, TimeoutError):
            result["private_paths"][name] = {"error_code": "OPENAI_CATALOG_UNVERIFIED"}
    if (
        isinstance(result["native"], dict)
        and "error_code" not in result["native"]
        and all(
            isinstance(p, dict) and "error_code" not in p
            for p in result["private_paths"].values()
        )
        and len(result["private_paths"]) == 3
    ):
        result["all_catalogs_reachable"] = True
        native = result["native"]
        private = result["private_paths"]
        k2_instances = native["loaded_k2_instance_count"]
        expected_on_all = all(
            p["expected_k2_model_id_present"] for p in private.values()
        )
        compatible_ids_empty = all(p["loaded_model_ids"] == 0 for p in private.values())
        if native["loaded_instance_count"] == 0 and compatible_ids_empty:
            result["status"] = "CATALOG_HEALTHY_ALL_MODELS_IDLE"
        elif k2_instances == 0 and not expected_on_all:
            result["status"] = "EXPECTED_K2_NOT_RESIDENT"
        elif k2_instances > 0 and expected_on_all:
            result["status"] = "CATALOGS_INDICATE_K2_LOAD_NOT_PHYSICAL_PROOF"
        else:
            result["status"] = "INCONSISTENT_RESIDENCY_CATALOGS_HOLD"
    # Even fully consistent catalogs are not independently verified
    # socket ownership, mapped GGUF, active tokens or signed admission.
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(collect(), sort_keys=True, separators=(",", ":")))
    # Every state is non-admitting, including a catalog claiming K2 loaded.
    return 2


if __name__ == "__main__":
    sys.exit(main())
