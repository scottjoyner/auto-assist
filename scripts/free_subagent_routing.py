"""Free-model subagent supervision slice — routing and pool classification.

Pure logic: pool tier mapping, provider state, model classification,
ordering, and qualification. No I/O, no state mutation, no secrets.

Read-only to routing/admission. Reuses scripts/opencode-bridge conventions.
"""

from __future__ import annotations

import collections
import json
import os
import pathlib
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

# Kilo anonymous pool: OpenAI-compatible, zero-cost, anonymous bearer.
KILO_ANONYMOUS_BASE_URL = "https://api.kilo.ai/api/openrouter"
KILO_ANONYMOUS_BEARER = "anonymous"

POOL_PREFERENCE_ORDER: tuple[str, ...] = (
    "kilo-anonymous",
    "lmstudio-local",
    "openrouter-free",
    "cohere-reserved",
    "zai-reserve",
)
RESERVE_ONLY_POOLS: frozenset[str] = frozenset({"zai-reserve"})
ANONYMOUS_POOLS: frozenset[str] = frozenset({"kilo-anonymous", "lmstudio-local"})

PROVIDER_STATES: tuple[str, ...] = (
    "usable",
    "rate_limited",
    "quota_exhausted",
    "payment_required",
    "unqualified",
)
_STATE_SEVERITY: dict[str, int] = {
    "payment_required": 0,
    "quota_exhausted": 1,
    "rate_limited": 2,
    "unqualified": 3,
}

ROUTE_ALIAS_TOKENS: frozenset[str] = frozenset({"free", "auto", "router", "any", "best", "default"})

KNOWN_PROVIDERS: frozenset[str] = frozenset({"openrouter", "kilo", "zai", "cohere", "cohère", "lmstudio", "opencode"})

_FREE_PREFIX_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("openrouter/", "openrouter"),
    ("kilo/", "kilo"),
    ("zai/", "zai"),
    ("lmstudio/", "lmstudio"),
    ("cohere/", "cohere"),
)

DEFAULT_FIXTURE_PATH = pathlib.Path("tests/fixtures/openrouter_models_sample.jsonl")
DEFAULT_PROVIDER_POOLS_FIXTURE_PATH = pathlib.Path("tests/fixtures/provider_pools_sample.jsonl")


def _opencode_binary() -> str:
    return (
        os.environ.get("OPENCODE_BIN")
        or shutil.which("opencode")
        or str(pathlib.Path.home() / ".opencode" / "bin" / "opencode")
    )


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _price_is_zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def pool_tier(provider: str, base_url: str = "") -> str:
    p = str(provider or "").lower()
    bu = str(base_url or "").lower()
    if p == "kilo" or "api.kilo.ai" in bu:
        return "kilo-anonymous"
    if "lmstudio" in p or "lm_studio" in p:
        return "lmstudio-local"
    if p == "openrouter":
        return "openrouter-free"
    if p in ("cohère", "cohere"):
        return "cohere-reserved"
    if p == "zai":
        return "zai-reserve"
    return "unqualified"


def is_anonymous_pool(tier: str) -> bool:
    return tier in ANONYMOUS_POOLS


def pool_preference_rank(tier: str) -> int:
    try:
        return POOL_PREFERENCE_ORDER.index(tier)
    except ValueError:
        return len(POOL_PREFERENCE_ORDER)


def route_kind(model_id: str) -> str:
    mid = str(model_id or "").strip()
    if "/" in mid:
        leaf = mid.rsplit("/", 1)[-1].lower()
        leaf = leaf.split(":")[0]
        if leaf in ROUTE_ALIAS_TOKENS:
            return "alias"
    return "exact"


def provider_state(record: dict[str, Any], observed: dict[str, Any] | None = None) -> str:
    observed = observed or {}
    for state in ("payment_required", "quota_exhausted", "rate_limited"):
        if observed.get(state):
            return state
    error_type = str(observed.get("error_type") or observed.get("kind") or "").lower()
    status_code = observed.get("status_code") or observed.get("statusCode")
    if error_type in {"provider_overloaded", "overloaded"} or status_code == 503:
        return "rate_limited"
    provider = str(record.get("provider") or "").lower()
    if provider and provider not in KNOWN_PROVIDERS:
        return "unqualified"
    pricing = record.get("pricing")
    pricing = pricing if isinstance(pricing, dict) else {}
    has_pricing = bool(pricing)
    zero_cost = _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion"))
    if has_pricing and not zero_cost:
        return "payment_required"
    if zero_cost:
        return "usable"
    if pool_tier(provider, record.get("base_url") or "") == "kilo-anonymous":
        return "usable"
    return "unqualified"


def _least_severe_state(states: list[str]) -> str:
    if not states:
        return "unqualified"
    return min(states, key=lambda s: _STATE_SEVERITY.get(s, len(_STATE_SEVERITY)))


def order_free_pools(
    models: list[dict[str, Any]] | None = None,
    allow_reserve: bool = False,
) -> list[dict[str, Any]]:
    candidates = models if models is not None else enumerate_free_models()
    ordered: list[tuple[int, int, dict[str, Any]]] = []
    for index, model in enumerate(candidates):
        observed = model.get("observed") if isinstance(model.get("observed"), dict) else None
        state = provider_state(model, observed)
        if state != "usable":
            continue
        tier = pool_tier(model.get("provider") or "", model.get("base_url") or "")
        if tier in RESERVE_ONLY_POOLS and not allow_reserve:
            continue
        ordered.append((pool_preference_rank(tier), index, model))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return [model for _, _, model in ordered]


def qualify_provider_pools(
    models: list[dict[str, Any]] | None = None,
    allow_reserve: bool = False,
) -> list[dict[str, Any]]:
    candidates = models if models is not None else enumerate_free_models()
    pools: dict[str, dict[str, Any]] = {}
    for model in candidates:
        tier = pool_tier(model.get("provider") or "", model.get("base_url") or "")
        observed = model.get("observed") if isinstance(model.get("observed"), dict) else None
        state = provider_state(model, observed)
        kind = route_kind(model.get("id") or "")
        entry = pools.setdefault(
            tier,
            {
                "pool": tier,
                "preference_rank": pool_preference_rank(tier),
                "anonymous": is_anonymous_pool(tier),
                "reserve_only": tier in RESERVE_ONLY_POOLS,
                "usable_models": [],
                "blocked": [],
                "route_kinds": collections.Counter(),
            },
        )
        entry["route_kinds"][kind] += 1
        if state == "usable":
            entry["usable_models"].append(model.get("id"))
        else:
            entry["blocked"].append({"model": model.get("id"), "state": state})

    result: list[dict[str, Any]] = []
    for tier in sorted(pools, key=lambda t: pool_preference_rank(t)):
        entry = pools[tier]
        if entry["usable_models"]:
            entry["state"] = "usable"
        else:
            entry["state"] = _least_severe_state([b["state"] for b in entry["blocked"]])
        entry["usable_model_count"] = len(entry["usable_models"])
        entry["route_kinds"] = dict(entry["route_kinds"])
        if entry["reserve_only"] and not allow_reserve:
            entry["policy"] = "reserve-only: excluded unless explicitly requested"
            entry["excluded_by_policy"] = True
        result.append(entry)
    return result


def _is_clearly_free_model(record: dict[str, Any]) -> bool:
    model_id = str(record.get("id") or "")
    if ":free" in model_id:
        return True
    tags = record.get("tags") or []
    if isinstance(tags, list) and any(str(tag).lower() == "free" for tag in tags):
        return True
    provider = str(record.get("provider") or "").lower()
    pricing = record.get("pricing")
    pricing = pricing if isinstance(pricing, dict) else {}
    if provider == "kilo":
        if not pricing:
            return True
        return _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion"))
    if not pricing:
        return False
    return _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion"))


def classify_free_model(record: dict[str, Any]) -> str:
    model_id = str(record.get("id") or "")
    provider = str(record.get("provider") or "").lower()
    if provider == "kilo":
        pricing = record.get("pricing") or {}
        if not isinstance(pricing, dict) or not pricing:
            return "kilo-anonymous"
        if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
            return "kilo-anonymous"
    if provider == "zai":
        return "zai-zero-cost"
    if provider in ("cohère", "cohere"):
        pricing = record.get("pricing") or {}
        if isinstance(pricing, dict):
            if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
                return "cohere-zero-cost"
    if "lmstudio" in provider or "lm_studio" in provider:
        return "lmstudio-zero-cost"
    if ":free" in model_id:
        return "opencode-native-free"
    tags = record.get("tags") or []
    if isinstance(tags, list) and any(str(tag).lower() == "free" for tag in tags):
        return "opencode-native-free"
    if "openrouter" in provider:
        pricing = record.get("pricing") or {}
        if isinstance(pricing, dict):
            if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
                return "openrouter-account-free"
    pricing = record.get("pricing") or {}
    if isinstance(pricing, dict):
        if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
            return "unknown-free"
    return "rate-limited"


def enumerate_free_models(fixture_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if fixture_path is not None and fixture_path.exists():
        with fixture_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("id") and _is_clearly_free_model(obj):
                        obj["free_model_type"] = classify_free_model(obj)
                        results.append(obj)
                except json.JSONDecodeError:
                    pass
        seen = set()
        deduped = []
        for r in results:
            rid = r.get("id")
            if rid and rid not in seen:
                seen.add(rid)
                deduped.append(r)
        return deduped
    try:
        proc = subprocess.run(
            [_opencode_binary(), "models"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                matched_provider = next(
                    (provider for prefix, provider in _FREE_PREFIX_PROVIDERS if prefix in line),
                    None,
                )
                if matched_provider is None:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("id"):
                        if _is_clearly_free_model(obj):
                            obj["free_model_type"] = classify_free_model(obj)
                            results.append(obj)
                except json.JSONDecodeError:
                    kilo_free_alias = matched_provider == "kilo" and line == "kilo/kilo-auto/free"
                    if ":free" in line or kilo_free_alias:
                        obj = {"id": line, "provider": matched_provider, "tags": ["free"]}
                        obj["free_model_type"] = classify_free_model(obj)
                        results.append(obj)
            seen = set()
            deduped = []
            for r in results:
                rid = r.get("id")
                if rid and rid not in seen:
                    seen.add(rid)
                    deduped.append(r)
            return deduped
    except Exception:
        pass
    seen = set()
    deduped = []
    for r in results:
        rid = r.get("id")
        if rid and rid not in seen:
            seen.add(rid)
            deduped.append(r)
    return deduped
