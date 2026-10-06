#!/usr/bin/env python3
"""Free-model subagent supervision slice — read-only to routing/admission.

Purpose:
  Make direct OpenCode free-model subagent execution safer and observable
  to a supervising agent without modifying authoritative routing, admission,
  dispatch, or approval paths. Reuses scripts/opencode-bridge conventions.

Provider-pool policy (read-only, advisory):
  Free-provider orchestration MUST prefer truly zero-cost / anonymous pools
  before any reserved quota. Preference order (most preferred first):
    1. kilo-anonymous  — Kilo anonymous pool (OpenAI-compatible base
       https://api.kilo.ai/api/openrouter, anonymous bearer, no credential).
    2. lmstudio-local  — local LM Studio inference (zero-cost, no network).
    3. openrouter-free — OpenRouter :free / account-free models (zero-cost
       but a shared, rate-limited free quota that still needs a credential).
    4. cohere-reserved — reserved zero-cost quota.
    5. zai-reserve     — Z.AI, reserve-only by policy: never selected unless
       explicitly requested (allow_reserve=True).

  Every candidate is classified with a provider state —
  usable / rate_limited / quota_exhausted / payment_required / unqualified —
  derived WITHOUT spending. A non-zero-price model is payment_required and is
  never auto-selected. Exact-model routes (a concrete model id such as
  ``zai/glm-4.7-flash``) are distinguished from router aliases (a generic
  router-selection token such as ``openrouter/free`` or ``kilo/auto``).

Noninteractive stdin hazard (Fleet Commander-style launches):
  If launched noninteractively (e.g., via a fleet commander or automation
  wrapper), stdin must be closed or redirected from /dev/null. Example:
      opencode run ... < /dev/null
  Otherwise opencode may wait at init for interactive input.
  This module is read-only and never terminates external processes.

No API key values are emitted. No production routing mutations occur.
No process termination is performed. No secrets are stored.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import pwd
import shutil
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from typing import Any

# Kilo anonymous pool: OpenAI-compatible, zero-cost, anonymous bearer.
# The bearer value below is a non-secret placeholder for the anonymous pool;
# the supervisor never reads, stores, or prints a real credential for it.
KILO_ANONYMOUS_BASE_URL = "https://api.kilo.ai/api/openrouter"
KILO_ANONYMOUS_BEARER = "anonymous"

# Provider pool tiers in strict preference order: truly zero-cost / anonymous
# pools before reserved quota. Z.AI is reserve-only by policy.
POOL_PREFERENCE_ORDER: tuple[str, ...] = (
    "kilo-anonymous",
    "lmstudio-local",
    "openrouter-free",
    "cohere-reserved",
    "zai-reserve",
)
RESERVE_ONLY_POOLS: frozenset[str] = frozenset({"zai-reserve"})
ANONYMOUS_POOLS: frozenset[str] = frozenset({"kilo-anonymous", "lmstudio-local"})

# Provider lifecycle states surfaced without spending.
PROVIDER_STATES: tuple[str, ...] = (
    "usable",
    "rate_limited",
    "quota_exhausted",
    "payment_required",
    "unqualified",
)
# Severity ranking used to collapse a pool's blocked candidates to one state.
_STATE_SEVERITY: dict[str, int] = {
    "payment_required": 0,
    "quota_exhausted": 1,
    "rate_limited": 2,
    "unqualified": 3,
}

# Router-selection tokens that delegate model choice to the provider router
# (router aliases) rather than naming one concrete model (exact-model route).
ROUTE_ALIAS_TOKENS: frozenset[str] = frozenset({"free", "auto", "router", "any", "best", "default"})

# Providers the supervisor can qualify. Anything else is unqualified.
KNOWN_PROVIDERS: frozenset[str] = frozenset({"openrouter", "kilo", "zai", "cohere", "cohère", "lmstudio", "opencode"})

# Model-id prefixes that may carry free models, mapped to their provider.
_FREE_PREFIX_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("openrouter/", "openrouter"),
    ("kilo/", "kilo"),
    ("zai/", "zai"),
    ("lmstudio/", "lmstudio"),
    ("cohere/", "cohere"),
)

# Convention reuse: projection files use compact sorted JSON.
_PROJECTION_KEYS = [
    "supervision_slice",
    "observed_at",
    "read_only",
    "suggested_scale_verdict",
    "free_models_found",
    "credential_present",
    "subagent_records",
    "duplicate_worktrees",
    "projections_note",
]

def _state_root() -> pathlib.Path:
    env_root = os.environ.get("ASSIST_SUBAGENT_STATE_ROOT")
    if env_root:
        return pathlib.Path(env_root).expanduser()
    account_home = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)
    return account_home / ".local" / "state" / "auto-assist" / "subagents"


DEFAULT_STATE_PATH = _state_root() / "state.jsonl"
DEFAULT_FIXTURE_PATH = pathlib.Path("tests/fixtures/openrouter_models_sample.jsonl")
DEFAULT_PROVIDER_POOLS_FIXTURE_PATH = pathlib.Path("tests/fixtures/provider_pools_sample.jsonl")

# Live Kilo anonymous catalog endpoint (read-only, anonymous bearer).
# Matches the OpenRouter-compatible /models endpoint served by Kilo's
# anonymous pool. The bearer value is a non-secret anonymous placeholder;
# the supervisor never reads, stores, or prints a real credential.
KILO_CATALOG_URL = "https://api.kilo.ai/api/openrouter/models"
KILO_CATALOG_BEARER = "anonymous"
DEFAULT_CATALOG_TIMEOUT = 10.0
DEFAULT_KILO_CATALOG_FIXTURE_PATH = pathlib.Path("tests/fixtures/kilo_catalog_sample.json")

# Router aliases that delegate model selection to the provider router rather
# than naming one concrete model. These are never substantive candidates —
# they resolve to whatever the router picks at request time.
_CATALOG_ALIAS_IDS: frozenset[str] = frozenset(
    {
        "kilo/kilo-auto/free",
        "openrouter/free",
        "kilo/free",
        "kilo/auto",
        "openrouter/auto",
    }
)


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
    """Map a provider (and optional base URL) to its pool tier.

    The tier is a routing-pool classification, independent of whether a
    specific model is zero-cost. Use :func:`provider_state` for that.
    """
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
    """True for truly zero-cost / anonymous pools that need no credential."""
    return tier in ANONYMOUS_POOLS


def pool_preference_rank(tier: str) -> int:
    """Lower rank == more preferred. Unknown tiers rank last.

    Ordering enforces the policy that truly zero-cost / anonymous pools are
    preferred before any reserved quota.
    """
    try:
        return POOL_PREFERENCE_ORDER.index(tier)
    except ValueError:
        return len(POOL_PREFERENCE_ORDER)


def route_kind(model_id: str) -> str:
    """Distinguish an exact-model route from a router alias.

    An exact-model route names one concrete model (``zai/glm-4.7-flash``,
    ``openrouter/nvidia/nemotron-3-ultra-550b-a55b:free``). A router alias
    delegates model selection to the provider router via a generic token
    (``openrouter/free``, ``kilo/auto``).
    """
    mid = str(model_id or "").strip()
    if "/" in mid:
        leaf = mid.rsplit("/", 1)[-1].lower()
        leaf = leaf.split(":")[0]
        if leaf in ROUTE_ALIAS_TOKENS:
            return "alias"
    return "exact"


def provider_state(record: dict[str, Any], observed: dict[str, Any] | None = None) -> str:
    """Return a provider state WITHOUT spending the model.

    States (see ``PROVIDER_STATES``): usable, rate_limited, quota_exhausted,
    payment_required, unqualified. A non-zero-price model is always
    ``payment_required`` and is never auto-selected. ``observed`` may carry
    read-only signals (e.g. from trace analysis) that upgrade the state to
    rate_limited / quota_exhausted / payment_required without any spend.
    """
    observed = observed or {}
    for state in ("payment_required", "quota_exhausted", "rate_limited"):
        if observed.get(state):
            return state
    # Upstream free-provider capacity failures are temporary availability
    # failures, not evidence that the model is paid or permanently exhausted.
    # Treat provider overload / HTTP 503 like a rate limit so pool ordering
    # fails closed and can retry/fall through to another zero-cost lane.
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
    # No pricing metadata: the Kilo anonymous pool is zero-cost by policy.
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
    """Order candidate models by pool preference, safest/cheapest first.

    Excludes any candidate whose provider state is not ``usable`` (so paid
    models are never auto-selected) and excludes reserve-only pools (Z.AI)
    unless ``allow_reserve`` is explicitly True.
    """
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
    """Return ordered provider-pool summaries with surfaced states.

    Each pool carries its preference rank, whether it is anonymous, whether
    it is reserve-only, its aggregate provider state, the usable models it
    offers, the blocked candidates (with the reason state), and the count of
    exact-model vs alias routes it exposes. Pools are ordered by preference
    (anonymous / zero-cost before reserved quota); reserve-only pools are
    flagged as excluded unless ``allow_reserve`` is True.
    """
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
    # The Kilo anonymous pool is zero-cost by policy even when a model record
    # carries no per-model pricing metadata. A non-zero price still disqualifies.
    if provider == "kilo":
        if not pricing:
            return True
        return _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion"))
    if not pricing:
        return False
    return _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion"))


def classify_free_model(record: dict[str, Any]) -> str:
    """Classify free model by provider and pricing model.

    This is a COST classification only. Routing preference and the
    reserve-only policy for Z.AI are enforced separately by
    :func:`order_free_pools` / :func:`qualify_provider_pools`.

    Returns one of:
    - 'kilo-anonymous': Kilo anonymous pool (zero-cost by policy)
    - 'openrouter-account-free': OpenRouter with zero prompt and completion
    - 'opencode-native-free': OpenCode-native :free models
    - 'zai-zero-cost': Direct Z.AI models (reserve-only by policy)
    - 'cohere-zero-cost': Direct Cohere zero-cost models
    - 'lmstudio-zero-cost': Local LM Studio models
    - 'unknown-free': Other zero-cost models (unclassified)
    - 'rate-limited': Model available but rate-limited
    """
    model_id = str(record.get("id") or "")
    provider = str(record.get("provider") or "").lower()

    # Kilo anonymous pool (zero-cost by policy; no credential needed).
    if provider == "kilo":
        pricing = record.get("pricing") or {}
        if not isinstance(pricing, dict) or not pricing:
            return "kilo-anonymous"
        if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
            return "kilo-anonymous"

    # Z.AI zero-cost models (reserve-only by policy; cost classification
    # only — never preferred by the routing layer).
    if provider == "zai":
        return "zai-zero-cost"

    # Cohere zero-cost models (provider-specific)
    if provider in ("cohère", "cohere"):
        pricing = record.get("pricing") or {}
        if isinstance(pricing, dict):
            if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
                return "cohere-zero-cost"

    # LM Studio zero-cost models (local inference)
    if "lmstudio" in provider or "lm_studio" in provider:
        return "lmstudio-zero-cost"

    # OpenCode-native free models (:free suffix or explicit free tag)
    # These take priority over general OpenRouter account-free checks
    if ":free" in model_id:
        return "opencode-native-free"
    tags = record.get("tags") or []
    if isinstance(tags, list) and any(str(tag).lower() == "free" for tag in tags):
        return "opencode-native-free"

    # OpenRouter account-free models (both prompt and completion zero)
    if "openrouter" in provider:
        pricing = record.get("pricing") or {}
        if isinstance(pricing, dict):
            if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
                return "openrouter-account-free"

    # Unknown or rate-limited
    pricing = record.get("pricing") or {}
    if isinstance(pricing, dict):
        if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
            return "unknown-free"

    return "rate-limited"


def enumerate_free_models(fixture_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Return models that are explicitly free or zero-cost in both directions.

    Each returned model includes a 'free_model_type' field classifying:
    - openrouter-account-free
    - opencode-native-free
    - zai-zero-cost
    - cohere-zero-cost
    - lmstudio-zero-cost
    - unknown-free
    - rate-limited
    """
    results: list[dict[str, Any]] = []
    # Try authoritative command first; if unavailable/offline, fall back.
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
                # Recognize every free-capable provider prefix, not only
                # openrouter, so the anonymous Kilo pool and local studios
                # are discoverable too.
                matched_provider = next(
                    (provider for prefix, provider in _FREE_PREFIX_PROVIDERS if prefix in line),
                    None,
                )
                if matched_provider is None:
                    continue
                # Basic extraction; if JSON, parse; else treat as id.
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("id"):
                        if _is_clearly_free_model(obj):
                            obj["free_model_type"] = classify_free_model(obj)
                            results.append(obj)
                except json.JSONDecodeError:
                    # Plain model listings have no pricing metadata. Normally
                    # require an explicit :free suffix. The one exception is
                    # Kilo's documented anonymous free router alias, which has
                    # the stable full OpenCode selector kilo/kilo-auto/free.
                    # Do not generalize this to arbitrary Kilo model IDs.
                    kilo_free_alias = (
                        matched_provider == "kilo"
                        and line == "kilo/kilo-auto/free"
                    )
                    if ":free" in line or kilo_free_alias:
                        obj = {
                            "id": line,
                            "provider": matched_provider,
                            "tags": ["free"],
                        }
                        obj["free_model_type"] = classify_free_model(obj)
                        results.append(obj)
            # Deduplicate by id
            seen = set()
            deduped = []
            for r in results:
                rid = r.get("id")
                if rid and rid not in seen:
                    seen.add(rid)
                    deduped.append(r)
            return deduped
    except Exception:
        pass  # fall through to fixture
    # Fixture fallback
    fixture = fixture_path or DEFAULT_FIXTURE_PATH
    if fixture.exists():
        with fixture.open("r", encoding="utf-8") as f:
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
                    continue
    # Deduplicate
    seen = set()
    deduped = []
    for r in results:
        rid = r.get("id")
        if rid and rid not in seen:
            seen.add(rid)
            deduped.append(r)
    return deduped


def fetch_kilo_catalog(
    url: str = KILO_CATALOG_URL,
    bearer: str = KILO_CATALOG_BEARER,
    timeout: float = DEFAULT_CATALOG_TIMEOUT,
) -> list[dict[str, Any]]:
    """Read-only fetch of the Kilo anonymous catalog with a timeout.

    Issues ``GET {url}`` with ``Authorization: Bearer {bearer}`` and returns
    the ``data`` array from the JSON response. Returns an empty list on
    network/timeout failure or malformed payload. No credentials are stored
    or emitted.
    """
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    if not isinstance(raw, dict):
        return []
    data = raw.get("data")
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def _derive_provider(model_id: str) -> str:
    """Derive a provider name from a model id prefix."""
    mid = str(model_id or "").strip().lower()
    for prefix, provider in _FREE_PREFIX_PROVIDERS:
        if mid.startswith(prefix):
            return provider
    return ""


def normalize_catalog_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw catalog record to a stable shape.

    Preserves ``context_length`` and ``supported_parameters`` exactly as
    provided by the catalog. Pricing is normalized to a dict with
    ``prompt`` and ``completion`` keys (defaulting to 0 when absent).
    Provider is derived from the model id prefix.
    """
    model_id = str(record.get("id") or "").strip()
    pricing = record.get("pricing")
    pricing = pricing if isinstance(pricing, dict) else {}
    supported = record.get("supported_parameters")
    if not isinstance(supported, list):
        supported = list(supported) if supported is not None else []
    return {
        "id": model_id,
        "provider": _derive_provider(model_id),
        "pricing": {
            "prompt": pricing.get("prompt", 0),
            "completion": pricing.get("completion", 0),
        },
        "context_length": record.get("context_length"),
        "supported_parameters": supported,
    }


def is_router_alias(model_id: str) -> bool:
    """True for router-selection aliases that delegate model choice.

    Recognizes ``kilo/kilo-auto/free``, ``openrouter/free`` and other
    router-selection tokens. These are never substantive candidates.
    """
    mid = str(model_id or "").strip().lower()
    if mid in _CATALOG_ALIAS_IDS:
        return True
    if "/" in mid:
        leaf = mid.rsplit("/", 1)[-1]
        leaf = leaf.split(":")[0]
        if leaf in ROUTE_ALIAS_TOKENS:
            return True
    return False


def is_exact_zero_cost(record: dict[str, Any]) -> bool:
    """True when record is an exact model ID with numeric-zero prompt AND
    completion pricing.

    Router aliases and records with missing or non-zero pricing in either
    direction are never considered exact zero-cost.
    """
    model_id = str(record.get("id") or "").strip()
    if not model_id or is_router_alias(model_id):
        return False
    pricing = record.get("pricing")
    pricing = pricing if isinstance(pricing, dict) else {}
    prompt = pricing.get("prompt")
    completion = pricing.get("completion")
    if prompt is None or completion is None:
        return False
    return _price_is_zero(prompt) and _price_is_zero(completion)


def filter_substantive_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reject router aliases; return only exact-model zero-cost records.

    Router aliases such as ``kilo/kilo-auto/free`` and ``openrouter/free`` are
    excluded. Records with non-numeric-zero pricing in either direction or
    with missing pricing are excluded. Normalization (via
    :func:`normalize_catalog_record`) is applied to retained records,
    preserving ``context_length`` and ``supported_parameters``. Deduplication
    is performed by stable model id.
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if not is_exact_zero_cost(record):
            continue
        normalized = normalize_catalog_record(record)
        nid = normalized["id"]
        if nid in seen:
            continue
        seen.add(nid)
        results.append(normalized)
    return results


def normalize_kilo_catalog(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize and filter raw catalog records into substantive candidates.

    Convenience wrapper: normalize → reject router aliases → keep only exact
    model IDs whose prompt AND completion pricing are numeric zero.
    Preserves ``context_length`` and ``supported_parameters``.
    """
    return filter_substantive_candidates(records)


def fetch_substantive_free_models(
    url: str = KILO_CATALOG_URL,
    bearer: str = KILO_CATALOG_BEARER,
    timeout: float = DEFAULT_CATALOG_TIMEOUT,
) -> list[dict[str, Any]]:
    """End-to-end: fetch catalog, normalize, filter to substantive zero-cost
    exact models. Network-dependent entry point.
    """
    raw = fetch_kilo_catalog(url=url, bearer=bearer, timeout=timeout)
    return normalize_kilo_catalog(raw)


def credential_present() -> bool:
    """Return True if an OpenRouter credential env var exists.
    Never reads or prints the secret value."""
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "OPENROUTER_API_KEY_2"):
        if os.environ.get(key):
            return True
    return False


def _load_state_unlocked(path: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    return records


def _save_state_unlocked(records: list[dict[str, Any]], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_root = _state_root().expanduser().resolve()
    try:
        parent_resolved = path.parent.expanduser().resolve()
        inside_private_root = (
            parent_resolved == state_root
            or state_root in parent_resolved.parents
        )
    except OSError:
        inside_private_root = False
    if inside_private_root:
        os.chmod(path.parent, 0o700)
    temp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temp_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(
                json.dumps(
                    rec,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            )
        f.flush()
        os.fsync(f.fileno())
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)
    os.chmod(path, 0o600)


@contextlib.contextmanager
def _state_lock(path: pathlib.Path):
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Only tighten a directory we own/create for the default private state
    # hierarchy. Callers may intentionally place a state file under shared
    # parents such as /tmp during tests; never chmod those parents.
    state_root = _state_root().expanduser().resolve()
    try:
        parent_resolved = path.parent.expanduser().resolve()
        inside_private_root = (
            parent_resolved == state_root
            or state_root in parent_resolved.parents
        )
    except OSError:
        inside_private_root = False
    if not parent_existed or inside_private_root:
        os.chmod(path.parent, 0o700)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_state(state_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    path = state_path or DEFAULT_STATE_PATH
    with _state_lock(path):
        return _load_state_unlocked(path)


def save_state(records: list[dict[str, Any]], state_path: pathlib.Path | None = None) -> None:
    path = state_path or DEFAULT_STATE_PATH
    with _state_lock(path):
        _save_state_unlocked(records, path)


def register_subagent(
    record: dict[str, Any],
    state_path: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Append a local subagent record with fail-closed model attribution.

    Real work should use an exact model ID. Router aliases are accepted only
    when the upstream resolved model is supplied, or when the caller
    explicitly marks the record allow_unresolved_alias for a qualification
    canary. This prevents an anonymous router alias from being mistaken for
    the model that actually performed a run.
    """
    record = dict(record)
    for k in ("title", "objective", "model", "worktree", "pid", "session", "status"):
        if k not in record:
            record[k] = ""

    requested = str(record.get("requested_model") or record.get("model") or "")
    resolved = str(record.get("resolved_model") or "").strip() or None
    kind = route_kind(requested)
    if kind == "alias" and not resolved and not record.get("allow_unresolved_alias"):
        raise ValueError(
            "router alias lacks resolved_model; use an exact model ID for real "
            "subagent work or explicitly mark a qualification canary"
        )

    record["requested_model"] = requested
    record["resolved_model"] = resolved
    record["route_kind"] = kind
    if resolved:
        record["effective_model"] = resolved
        record["model_attribution"] = "resolved-upstream"
        record["model_identity_complete"] = True
    elif requested and kind == "exact":
        record["effective_model"] = requested
        record["model_attribution"] = "exact-request"
        record["model_identity_complete"] = True
    else:
        record["effective_model"] = None
        record["model_attribution"] = "unresolved-router-alias"
        record["model_identity_complete"] = False

    if "created_at" not in record:
        record["created_at"] = _now_utc()
    record["updated_at"] = _now_utc()

    path = state_path or DEFAULT_STATE_PATH
    with _state_lock(path):
        records = _load_state_unlocked(path)
        records.append(record)
        _save_state_unlocked(records, path)
    return records


def update_subagent(
    session_id: str,
    updates: dict[str, Any],
    state_path: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Atomically update one subagent record by session id."""
    path = state_path or DEFAULT_STATE_PATH
    with _state_lock(path):
        records = _load_state_unlocked(path)
        for record in records:
            current = str(record.get("session") or record.get("session_id") or "")
            if current != session_id:
                continue
            record.update(updates)
            record["updated_at"] = _now_utc()
            _save_state_unlocked(records, path)
            return records
    raise KeyError(f"unknown subagent session: {session_id}")


def inspect_subagents(state_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    return load_state(state_path)


def _opencode_db_uri() -> str:
    db_path = os.path.join(os.path.expanduser("~"), ".local", "share", "opencode", "opencode.db")
    return f"file:{db_path}?mode=ro"


def discover_live_sessions(query_only: bool = True) -> list[dict[str, Any]]:
    """SQLite read-only / query_only session discovery; no mutation.
    OpenCode time_updated is milliseconds; derived path from HOME."""
    import sqlite3
    import time

    db_uri = _opencode_db_uri()
    conn = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
    try:
        conn.execute("PRAGMA query_only = ON")
        # Prove mode=ro + PRAGMA query_only: a write must fail.
        try:
            conn.execute("CREATE TEMP TABLE _assert_write_fail (id INTEGER)")
            raise AssertionError("Write succeeded despite mode=ro and PRAGMA query_only")
        except sqlite3.OperationalError:
            pass  # expected failure
        cur = conn.cursor()
        now_ms = int(time.time() * 1000)
        seven_days_ms = 7 * 24 * 60 * 60 * 1000
        cur.execute(
            "SELECT id, title, slug, directory, agent, model, time_updated, time_archived FROM session WHERE time_updated > ? ORDER BY time_updated DESC LIMIT 50",
            (now_ms - seven_days_ms,),
        )
        cols = [d[0] for d in cur.description]
        rows = []
        for row in cur.fetchall():
            raw = dict(zip(cols, row, strict=False))
            # Map to duplicate/stale fields used by supervisor logic.
            session_id = raw.get("id") or raw.get("slug") or ""
            worktree = raw.get("directory") or ""
            agent = raw.get("agent") or ""
            model_raw = raw.get("model")
            model_name = ""
            provider = agent
            try:
                if isinstance(model_raw, str) and model_raw:
                    parsed = json.loads(model_raw)
                    if isinstance(parsed, dict):
                        model_name = parsed.get("id") or parsed.get("name") or ""
                        if parsed.get("providerID"):
                            provider = parsed.get("providerID")
                        elif parsed.get("provider"):
                            provider = parsed.get("provider")
            except Exception:
                model_name = str(model_raw) if model_raw is not None else ""
            updated_at_ms = raw.get("time_updated")
            updated_at = ""
            if updated_at_ms is not None:
                try:
                    ts = datetime.fromtimestamp(updated_at_ms / 1000.0, tz=UTC)
                    updated_at = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    updated_at = str(updated_at_ms)
            status = "archived" if raw.get("time_archived") is not None else "active"
            rows.append(
                {
                    "session": session_id,
                    "worktree": worktree,
                    "model": model_name,
                    "provider": provider,
                    "updated_at": updated_at,
                    "status": status,
                    "slug": raw.get("slug"),
                    "title": raw.get("title"),
                }
            )
        return rows
    finally:
        conn.close()


def detect_duplicate_worktrees(records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Detect active records with conflicting worktrees."""
    recs = records if records is not None else load_state()
    active = [
        r
        for r in recs
        if str(r.get("status") or "").lower() in ("active", "running", "in_progress")
        and not (r.get("source") == "sqlite_readonly" and not r.get("pid"))
    ]
    conflicts: list[dict[str, Any]] = []
    worktree_map: dict[str, list[dict[str, Any]]] = {}
    for r in active:
        wt = str(r.get("worktree") or "").strip()
        if wt:
            worktree_map.setdefault(wt, []).append(r)
    for wt, group in worktree_map.items():
        if len(group) > 1:
            conflicts.append(
                {
                    "worktree": wt,
                    "conflicting_records": len(group),
                    "pids": [r.get("pid") for r in group],
                    "sessions": [r.get("session") for r in group],
                    "titles": [r.get("title") for r in group],
                }
            )
    return conflicts


def detect_looping_records(records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Detect records with repeated session identifiers (looping registration)."""
    recs = records if records is not None else load_state()
    session_counts: dict[str, int] = {}
    for r in recs:
        sess = str(r.get("session") or "").strip()
        if sess:
            session_counts[sess] = session_counts.get(sess, 0) + 1
    loops = []
    for r in recs:
        sess = str(r.get("session") or "").strip()
        if sess and session_counts.get(sess, 0) > 1:
            loops.append(
                {
                    "session": sess,
                    "record_title": r.get("title"),
                    "record_pid": r.get("pid"),
                    "record_status": r.get("status"),
                    "occurrences": session_counts[sess],
                }
            )
            # Deduplicate loop entries per session by removing after first
            # but keep structure simple: return unique loop groups
    # Deduplicate by session
    seen = set()
    deduped = []
    for item in loops:
        if item["session"] not in seen:
            seen.add(item["session"])
            deduped.append(
                {
                    "session": item["session"],
                    "occurrences": item["occurrences"],
                    "titles": [rec.get("title") for rec in recs if str(rec.get("session") or "") == item["session"]],
                    "pids": [rec.get("pid") for rec in recs if str(rec.get("session") or "") == item["session"]],
                }
            )
    return deduped


def detect_stale_records(
    records: list[dict[str, Any]] | None = None, max_age_minutes: int = 30
) -> list[dict[str, Any]]:
    """Detect subagent records whose updated_at is older than max_age_minutes."""
    recs = records if records is not None else load_state()
    stale: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for r in recs:
        ts_str = str(r.get("updated_at") or r.get("created_at") or "").strip()
        if not ts_str:
            continue
        try:
            # Handle Z suffix
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            age_minutes = (now - ts).total_seconds() / 60.0
            if age_minutes > max_age_minutes:
                stale.append(
                    {
                        "session": r.get("session"),
                        "worktree": r.get("worktree"),
                        "updated_at": ts_str,
                        "status": r.get("status"),
                        "age_minutes": round(age_minutes, 2),
                    }
                )
        except Exception:
            continue
    # Deduplicate by session+worktree
    seen = set()
    deduped = []
    for item in stale:
        key = (str(item.get("session") or ""), str(item.get("worktree") or ""))
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _annotate_trace_session(record: dict[str, Any]) -> dict[str, Any]:
    """Annotate an exported session with fail-closed model attribution.

    Router aliases are not considered model-attributed unless the provider
    reported a concrete resolved model. Exact-model routes are attributable by
    the requested model contract. Never mutates the exporter's output.
    """
    annotated = dict(record)
    requested = str(annotated.get("requested_model") or annotated.get("model") or "")
    resolved = annotated.get("resolved_model")
    annotated.setdefault("requested_model", requested)
    annotated.setdefault("resolved_model", resolved)
    kind = route_kind(requested)
    annotated["route_kind"] = kind

    if resolved:
        annotated["effective_model"] = resolved
        annotated["model_attribution"] = "resolved-upstream"
        annotated["model_identity_complete"] = True
    elif requested and kind == "exact":
        annotated["effective_model"] = requested
        annotated["model_attribution"] = "exact-request"
        annotated["model_identity_complete"] = True
    else:
        annotated["effective_model"] = None
        annotated["model_attribution"] = "unresolved-router-alias"
        annotated["model_identity_complete"] = False
    return annotated


def emit_projection(
    free_models: list[dict[str, Any]] | None = None,
    state_path: pathlib.Path | None = None,
    trace_exporter_path: pathlib.Path | None = None,
    extra_note: str = "",
    allow_reserve: bool = False,
) -> dict[str, Any]:
    """Read-only supervision projection; never enforced.

    Surfaces provider pools ordered by the routing policy (truly
    zero-cost / anonymous pools before reserved quota), marks
    reserve-only pools (Z.AI) as excluded unless ``allow_reserve``
    is explicitly True, and reports each pool's provider state
    without spending any model.
    """
    models = free_models if free_models is not None else enumerate_free_models()

    # Classify models by cost type.
    model_types: dict[str, int] = {}
    for m in models:
        mtype = m.get("free_model_type", classify_free_model(m))
        model_types[mtype] = model_types.get(mtype, 0) + 1

    # Provider-pool qualification: ordered, state-surfaced, reserve-aware.
    provider_pools = qualify_provider_pools(models, allow_reserve=allow_reserve)
    preferred_pool_entry = next(
        (p for p in provider_pools if p.get("state") == "usable" and not p.get("reserve_only")),
        None,
    )
    preferred_pool = preferred_pool_entry.get("pool") if preferred_pool_entry else None
    reserve_only_pools = [
        {"pool": p["pool"], "policy": p.get("policy", "reserve-only")} for p in provider_pools if p.get("reserve_only")
    ]
    anonymous_pools_usable = [p["pool"] for p in provider_pools if p.get("anonymous") and p.get("state") == "usable"]
    route_kind_counts: dict[str, int] = {}
    for m in models:
        kind = route_kind(m.get("id") or "")
        route_kind_counts[kind] = route_kind_counts.get(kind, 0) + 1

    records = inspect_subagents(state_path)
    session_discovery_error: str | None = None
    if not records:
        try:
            live = discover_live_sessions(query_only=True)
            if live:
                records = [{"source": "sqlite_readonly", **r} for r in live]
        except Exception as exc:
            session_discovery_error = f"{type(exc).__name__}: {exc}"
    duplicates = detect_duplicate_worktrees(records)
    loops = detect_looping_records(records)
    stale = detect_stale_records(records)
    cred = credential_present()
    # Suggested-but-not-enforced verdict. A missing OpenRouter
    # credential only forces hold when no anonymous / zero-cost pool
    # is usable without a credential (e.g. the Kilo anonymous pool
    # or local LM Studio).
    usable_anonymous = bool(anonymous_pools_usable)
    verdict = "healthy"
    if duplicates or loops or stale or not models or session_discovery_error:
        verdict = "hold"
    if not cred and not usable_anonymous:
        verdict = "hold"

    projection = {
        "supervision_slice": "free_subagent_supervisor",
        "observed_at": _now_utc(),
        "read_only": True,
        "suggested_scale_verdict": verdict,
        "free_models_found": len(models),
        "credential_present": cred,
        "subagent_records": len(records),
        "session_discovery_error": session_discovery_error,
        "duplicate_worktrees": duplicates,
        "looping_records": loops,
        "stale_records": stale,
        "free_model_ids": sorted([m.get("id") for m in models if m.get("id")]),
        "free_model_types": model_types,
        "free_route_kinds": route_kind_counts,
        "provider_pools": provider_pools,
        "preferred_pool": preferred_pool,
        "reserve_only_pools": reserve_only_pools,
        "anonymous_pools_usable": anonymous_pools_usable,
        "kilo_anonymous_base_url": KILO_ANONYMOUS_BASE_URL,
        "projections_note": (
            "Projection is read-only and advisory. It does not approve, admit, "
            "dispatch, or terminate subagent processes. Scale verdict is suggested, "
            "not enforced. Close stdin (< /dev/null) for noninteractive launches to "
            "avoid init waits. Free-cost gating requires both prompt and completion "
            "pricing to be zero; stale/duplicate/looping classifications are advisory. "
            "Provider pools are ordered to prefer truly zero-cost/anonymous pools "
            "(Kilo anonymous, LM Studio local) before reserved quota; Z.AI is "
            "reserve-only and excluded unless explicitly requested; provider state "
            "is surfaced as usable/rate_limited/quota_exhausted/payment_required/"
            "unqualified without spending paid models." + (" " + extra_note if extra_note else "")
        ),
    }

    # Read-only trace exporter integration (never mutates state).
    if trace_exporter_path and trace_exporter_path.exists():
        try:
            import importlib.util

            SPEC = importlib.util.spec_from_file_location("trace_exporter", trace_exporter_path)
            trace_exporter_module = importlib.util.module_from_spec(SPEC)
            assert SPEC.loader is not None
            SPEC.loader.exec_module(trace_exporter_module)

            # OpenCode SQLite read-only path
            opencode_db_path = pathlib.Path.home() / ".local" / "share" / "opencode" / "opencode.db"
            if opencode_db_path.exists():
                raw_records = trace_exporter_module.export_sessions(opencode_db_path)
                trace_records = [_annotate_trace_session(r) for r in raw_records]
                projection["trace_records_count"] = len(trace_records)
                projection["trace_records_sample"] = [
                    {
                        k: v
                        for k, v in r.items()
                        if k
                        in (
                            "session_id",
                            "provider",
                            "requested_model",
                            "resolved_model",
                            "effective_model",
                            "route_kind",
                            "model_attribution",
                            "model_identity_complete",
                            "directory",
                            "cost",
                            "tokens",
                            "tools",
                            "git",
                            "time_created",
                            "time_updated",
                        )
                    }
                    for r in trace_records[:10]
                ]
                projection["trace_provider_summary"] = {}
                projection["trace_route_kind_summary"] = {}
                projection["trace_model_attribution_summary"] = {}
                projection["trace_unresolved_router_aliases"] = []
                for r in trace_records:
                    provider = r.get("provider", "unknown")
                    projection["trace_provider_summary"][provider] = (
                        projection["trace_provider_summary"].get(provider, 0) + 1
                    )
                    kind = r.get("route_kind", "exact")
                    projection["trace_route_kind_summary"][kind] = (
                        projection["trace_route_kind_summary"].get(kind, 0) + 1
                    )
                    attribution = r.get("model_attribution", "unknown")
                    projection["trace_model_attribution_summary"][attribution] = (
                        projection["trace_model_attribution_summary"].get(attribution, 0) + 1
                    )
                    if attribution == "unresolved-router-alias":
                        projection["trace_unresolved_router_aliases"].append(
                            {
                                "session_id": r.get("session_id"),
                                "provider": r.get("provider"),
                                "requested_model": r.get("requested_model"),
                                "directory": r.get("directory"),
                            }
                        )
        except Exception as e:
            projection["trace_exporter_error"] = str(e)

    return projection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Free subagent supervisor (read-only).")
    parser.add_argument("--enumerate-models", action="store_true", help="List free model IDs")
    parser.add_argument("--credential-present", action="store_true", help="Report credential presence (bool only)")
    parser.add_argument("--inspect-state", action="store_true", help="Show subagent records (JSON lines)")
    parser.add_argument("--register-subagent", type=str, default="", help="JSON record to register")
    parser.add_argument("--detect-duplicates", action="store_true", help="Detect duplicate worktrees")
    parser.add_argument("--detect-looping", action="store_true", help="Detect looping session registrations")
    parser.add_argument("--detect-stale", action="store_true", help="Detect stale subagent records")
    parser.add_argument("--projection", action="store_true", help="Emit supervision projection")
    parser.add_argument("--provider-pools", action="store_true", help="Emit qualified provider pools (read-only)")
    parser.add_argument(
        "--allow-reserve", action="store_true", help="Explicitly request reserve-only pools (e.g. Z.AI)"
    )
    parser.add_argument(
        "--trace", type=pathlib.Path, default=None, help="Use trace exporter for projection (read-only)"
    )
    parser.add_argument("--fixture", type=str, default=str(DEFAULT_FIXTURE_PATH), help="Fixture path")
    parser.add_argument(
        "--pools-fixture",
        type=str,
        default=str(DEFAULT_PROVIDER_POOLS_FIXTURE_PATH),
        help="Provider-pools fixture path",
    )
    parser.add_argument("--state", type=str, default=str(DEFAULT_STATE_PATH), help="State file path")
    parser.add_argument(
        "--kilo-catalog",
        action="store_true",
        help="Fetch Kilo anonymous catalog and list substantive zero-cost models (read-only, network)",
    )
    parser.add_argument(
        "--catalog-fixture",
        type=str,
        default=None,
        help="JSON fixture for --kilo-catalog (no network)",
    )
    parser.add_argument(
        "--catalog-timeout",
        type=float,
        default=DEFAULT_CATALOG_TIMEOUT,
        help="Kilo catalog fetch timeout in seconds",
    )
    args = parser.parse_args(argv)

    state_path = pathlib.Path(args.state)
    fixture_path = pathlib.Path(args.fixture) if args.fixture else None

    if args.enumerate_models:
        for m in enumerate_free_models(fixture_path):
            print(
                json.dumps(
                    {
                        "id": m.get("id"),
                        "tags": m.get("tags", []),
                        "free_model_type": m.get("free_model_type", classify_free_model(m)),
                    }
                )
            )
        return 0

    if args.credential_present:
        # Only boolean; never secret
        print("true" if credential_present() else "false")
        return 0

    if args.register_subagent:
        try:
            rec = json.loads(args.register_subagent)
            records = register_subagent(rec, state_path)
            print(json.dumps({"registered": True, "total_records": len(records)}))
        except Exception as e:
            print(json.dumps({"registered": False, "error": str(e)}))
            return 1
        return 0

    if args.inspect_state:
        for rec in inspect_subagents(state_path):
            print(json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.detect_duplicates:
        dups = detect_duplicate_worktrees(inspect_subagents(state_path))
        print(json.dumps({"duplicates": dups}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.detect_looping:
        loops = detect_looping_records(inspect_subagents(state_path))
        print(json.dumps({"looping": loops}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.detect_stale:
        stale = detect_stale_records(inspect_subagents(state_path))
        print(json.dumps({"stale": stale}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.provider_pools:
        pools_path = pathlib.Path(args.pools_fixture) if args.pools_fixture else None
        models = enumerate_free_models(pools_path)
        qualified = qualify_provider_pools(models, allow_reserve=args.allow_reserve)
        print(
            json.dumps(
                {
                    "read_only": True,
                    "allow_reserve": args.allow_reserve,
                    "kilo_anonymous_base_url": KILO_ANONYMOUS_BASE_URL,
                    "preferred_pool": next(
                        (p["pool"] for p in qualified if p.get("state") == "usable" and not p.get("reserve_only")),
                        None,
                    ),
                    "provider_pools": qualified,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        return 0

    if args.projection:
        proj = emit_projection(
            free_models=enumerate_free_models(fixture_path),
            state_path=state_path,
            trace_exporter_path=args.trace,
            allow_reserve=args.allow_reserve,
        )
        print(json.dumps(proj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.kilo_catalog:
        if args.catalog_fixture and pathlib.Path(args.catalog_fixture).exists():
            raw_doc = json.loads(pathlib.Path(args.catalog_fixture).read_text(encoding="utf-8"))
            records = raw_doc.get("data", []) if isinstance(raw_doc, dict) else raw_doc
        else:
            records = fetch_kilo_catalog(timeout=args.catalog_timeout)
        candidates = normalize_kilo_catalog(records)
        print(
            json.dumps(
                {
                    "read_only": True,
                    "network": args.catalog_fixture is None,
                    "substantive_candidates": candidates,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        return 0

    # Default: projection with trace
    proj = emit_projection(
        free_models=enumerate_free_models(fixture_path),
        state_path=state_path,
        trace_exporter_path=args.trace,
        allow_reserve=args.allow_reserve,
    )
    print(json.dumps(proj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
