"""Read-only Auto-Router provider inventory compatibility.

The effective snapshot is authoritative ONLY about router memory at capture.
Configured provider models are NOT evidence of model residency or health.
No probe, inference request, config reload or dispatch is performed here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit


def _text(value: object, *, limit: int = 128) -> str:
    return value[:limit].strip().lower() if isinstance(value, str) else ""


def _model_name(value: object) -> str:
    """Keep model spelling intact: downstream model IDs may be case-sensitive."""
    return value.strip()[:120] if isinstance(value, str) else ""


def _catalog_rows(rows: object, *, effective: bool) -> dict[str, list[str]]:
    if not isinstance(rows, list) or len(rows) > 1000:
        return {}
    found: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return {}
        enabled_key = "enabled_in_router_memory" if effective else "enabled"
        if row.get(enabled_key) is not True:
            continue
        models_key = "configured_models" if effective else "models"
        models = row.get(models_key)
        if not isinstance(models, list) or len(models) > 100:
            return {}
        endpoints: list[str] = []
        if effective:
            endpoints_raw = [row.get("effective_base_url")]
            access = row.get("effective_access_urls", [])
            if not isinstance(access, list) or len(access) > 12:
                return {}
            endpoints_raw.extend(access)
            for endpoint in endpoints_raw:
                if not isinstance(endpoint, dict):
                    continue
                if endpoint.get("status") != "PARSED_NOT_PROBED":
                    continue
                endpoints.append(_text(endpoint.get("hostname"), limit=253))
        else:
            base = row.get("base_url", "")
            if isinstance(base, str) and len(base) <= 2048:
                try:
                    endpoints.append(_text(urlsplit(base).hostname, limit=253))
                except ValueError:
                    pass
        identities = (
            _text(row.get("node_id")),
            _text(row.get("provider_id" if effective else "runtime_instance_id")),
            *endpoints,
        )
        for m in models:
            if not isinstance(m, dict):
                return {}
            model = (
                _model_name(m.get("provider_model"))
                or _model_name(m.get("alias"))
                or ("" if effective else _model_name(m.get("model_instance_id")))
            )
            if not model:
                continue
            for identity in identities:
                if identity:
                    bucket = found.setdefault(identity, [])
                    if model not in bucket:
                        bucket.append(model)
    return found


def parse_provider_inventory(data: object, *, effective: bool) -> dict[str, list[str]]:
    """Fail closed for malformed data and reject raw/legacy data at effective path."""
    if not isinstance(data, dict):
        return {}
    rows = data.get("providers")
    if effective:
        if (
            type(data.get("schema_version")) is not int
            or data["schema_version"] != 1
            or data.get("kind") != "EFFECTIVE_ROUTER_MEMORY_READ_ONLY"
            or data.get("state_source") != "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE"
            or data.get("no_probes_or_dispatch") is not True
            or data.get("may_prove_model_loaded") is not False
            or data.get("may_modify_routes") is not False
            or type(data.get("provider_count")) is not int
            or not isinstance(rows, list)
            or data["provider_count"] != len(rows)
        ):
            return {}
    return _catalog_rows(rows, effective=effective)


def fetch_provider_inventory(
    *,
    base_url: str,
    override_url: str,
    admin_token: str,
    fetch_json: Callable[..., tuple[dict[str, Any], float]],
) -> dict[str, list[str]]:
    """Prefer sanitized snapshot; use authenticated legacy only on exact 404.

    Explicit legacy URL overrides are preserved for existing operator rollout.
    Authentication failures, invalid snapshots and outages NEVER downgrade.
    """
    if not admin_token:
        return {}
    override = override_url.strip()
    base = base_url.strip().rstrip("/")
    if not override and not base:
        return {}
    effective_url = f"{base}/admin/providers/effective" if not override else override
    is_legacy_override = bool(override) and not override.endswith("/admin/providers/effective")
    try:
        data, _ = fetch_json(effective_url, "", admin_token=admin_token)
    except HTTPError as err:
        if override or err.code != 404:
            return {}
        # Only compatibility fallback for a pre-PR41 router; still uses auth.
        try:
            data, _ = fetch_json(f"{base}/admin/providers", "", admin_token=admin_token)
        except Exception:
            return {}
        return parse_provider_inventory(data, effective=False)
    except Exception:
        return {}
    return parse_provider_inventory(data, effective=not is_legacy_override)
