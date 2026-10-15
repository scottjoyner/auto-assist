#!/usr/bin/env python3
"""No-network, no-credential evidence gate for an OpenCode /provider snapshot.

Read JSON via --input or stdin. Project ONLY allowlisted IDs and metadata;
do not echo tokens, unlisted provider configurations, paths, or input.
Catalog $0 is *not* evidence of a real zero-cost entitlement/usage receipt.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys
from collections.abc import Mapping
from typing import Any

SCHEMA = "free-provider-catalog-readiness/v1"
MAX_BYTES = 8 * 1024 * 1024
CANDIDATES: dict[str, tuple[str, ...]] = {
    "zai": ("glm-4.7-flash", "glm-4.5-flash", "glm-4.6v-flash"),
    "cohere": ("north-mini-code-1-0",),
    "opencode": ("ling-3.1-flash-free", "mimo-v2.6-flash-free", "nemotron-3-ultra-free"),
    "openrouter": ("thinkingmachines/inkling-small:free", "cohere/north-mini-code:free"),
    "kilo_free": ("nvidia/nemotron-3-ultra-550b-a55b:free",
                  "dots-studio/dots-3-note-preview:free"),
}
ALIASES = frozenset({"free", "auto", "router", "any", "best", "default"})


def exact_identifier(model: str) -> bool:
    if not isinstance(model, str) or not model or model.strip() != model:
        return False
    if "\n" in model or "\r" in model or "\\" in model or " " in model:
        return False
    parts = model.split("/")
    if any(not p for p in parts):
        return False
    leaf = parts[-1].split(":", 1)[0].lower()
    return bool(leaf) and leaf not in ALIASES


def exact_zero(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        n = Decimal(str(value))
        return n.is_finite() and n == 0
    except (InvalidOperation, ValueError, TypeError):
        return False


def _provider_rows(registry: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = registry.get("all", [])
    if not isinstance(rows, list):
        return {}
    safe = {}
    ambiguous = set()
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        provider = item.get("id")
        if not isinstance(provider, str) or provider not in CANDIDATES:
            continue
        if provider in safe or provider in ambiguous:
            # Refuse to pick a favorable row from a duplicate provider.
            # The whole provider must be considered unqualified.
            ambiguous.add(provider)
            safe.pop(provider, None)
        else:
            safe[provider] = item
    return safe


def catalog_readiness(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Strict catalog-only output; will never authorize generation."""
    if not isinstance(registry, Mapping):
        raise ValueError("invalid_provider_registry")
    connected = registry.get("connected")
    if not isinstance(connected, list):
        connected = []
    connected = {p for p in connected if isinstance(p, str) and p in CANDIDATES}
    providers = _provider_rows(registry)
    rows = []
    for provider_id, models in sorted(CANDIDATES.items()):
        data = providers.get(provider_id, {})
        entries = data.get("models") if isinstance(data, Mapping) else None
        entries = entries if isinstance(entries, Mapping) else {}
        for model_id in sorted(models):
            meta = entries.get(model_id)
            meta = meta if isinstance(meta, Mapping) else {}
            price = meta.get("cost")
            price = price if isinstance(price, Mapping) else {}
            # A missing provider, inactive route or nonnumeric price is a denial.
            present = model_id in entries and bool(meta)
            active = meta.get("status") == "active"
            connected_now = provider_id in connected
            catalog_zero = exact_zero(price.get("input")) and exact_zero(price.get("output"))
            can_consider = bool(present and connected_now and active and catalog_zero
                                and exact_identifier(model_id))
            reason = (
                "model_missing" if not present else
                "provider_not_connected" if not connected_now else
                "inactive_or_unknown" if not active else
                "cost_missing_or_nonzero" if not catalog_zero else
                "route_alias_or_malformed" if not exact_identifier(model_id) else
                "catalog_only_unqualified"
            )
            rows.append({
                "provider": provider_id,
                "model": model_id,
                "catalog_present": bool(present),
                "provider_connected": bool(connected_now),
                "catalog_status_active": bool(active),
                "advertised_zero_input_output": bool(catalog_zero),
                "catalog_candidate": can_consider,
                "qualification": "unqualified",
                "quota_account_witness": "absent",
                "independent_upstream_group": "unverified",
                "generation_receipt": "absent",
                "provider_calls": 0,
                "dispatch_authorized": False,
                "reason": reason,
            })
    return {
        "schema": SCHEMA,
        "source": "supplied_opencode_provider_registry_read_only",
        "records": rows,
        "candidate_routes": len(rows),
        "catalog_candidates": sum(int(x["catalog_candidate"]) for x in rows),
        "independent_qualified_quota_groups": 0,
        "dispatch_authorized": False,
        "live_generation_calls": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Existing /provider JSON file; stdin otherwise")
    args = parser.parse_args(argv)
    if args.input:
        if args.input.stat().st_size > MAX_BYTES:
            parser.error("input_too_large")
        source = args.input.open("rb")
    else:
        source = sys.stdin.buffer
    try:
        data = source.read(MAX_BYTES + 1)
    finally:
        if args.input:
            source.close()
    if len(data) > MAX_BYTES:
        parser.error("input_too_large")
    try:
        parsed = json.loads(data)
        projected = catalog_readiness(parsed)
    except (ValueError, TypeError):
        parser.error("invalid_provider_registry")
    print(json.dumps(projected, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
