"""B02 shadow-only privacy and operator authentication contract.
No route is mounted. No network, Neo4j or secret/config reads.
Use the existing independently verified Basic + operator allowlist helper.
A positive fixture permits only *synthetic* shadow tests, NEVER production.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from assistx.trace_preview_access import basic_preview_permitted

class ShadowDenied(ValueError):
    pass

@dataclass(frozen=True)
class ShadowPolicy:
    operator_users: str = ""
    reviewed_global_operator_scope: bool = False
    explicit_shadow_mode: bool = False

@dataclass(frozen=True)
class ShadowEvidence:
    principal: str
    mode: str = "offline_operator_shadow_only"
    real_data_access_authorized: bool = False
    production_route_authorized: bool = False
    tenant_isolation_proven: bool = False
    network_cutover_authorized: bool = False
    release_authorized: bool = False

def authorize_shadow(*, principal: str, credentials,
                     configured_user: str | None,
                     configured_password: str | None,
                     policy: ShadowPolicy) -> ShadowEvidence:
    """Unverified trusted headers have no authorization role."""
    if (type(policy) is not ShadowPolicy
            or policy.explicit_shadow_mode is not True
            or policy.reviewed_global_operator_scope is not True):
        raise ShadowDenied("SHADOW_POLICY_NOT_APPROVED")
    if not basic_preview_permitted(
            principal, credentials,
            configured_user=configured_user,
            configured_password=configured_password,
            allowed_users=policy.operator_users):
        raise ShadowDenied("VERIFIED_BASIC_OPERATOR_REQUIRED")
    return ShadowEvidence(principal=principal)

_PAYLOAD_KEYS = frozenset({"endpoint", "suite_id", "chain", "fail_set"})
_CHAIN_KEYS = frozenset({"chain_id"})
_FAIL_KEYS = frozenset({"task_id", "expected", "actual"})
_MAX_BYTES = 65536
_MAX_FAILS = 32
_MAX_TEXT = 2048

def _text(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or len(value.encode("utf-8")) > _MAX_TEXT:
        raise ShadowDenied("UNREVIEWED_PAYLOAD_VALUE_" + key.upper())
    if key == "endpoint" and any(marker in value for marker in ("@", "?", "#")):
        raise ShadowDenied("ENDPOINT_MAY_CONTAIN_CREDENTIAL_OR_QUERY_DATA")
    return value

def minimize_payload(raw: Any) -> str | None:
    """Strict view-field allowlist. Still NOT a PII classification."""
    if raw is None:
        return None
    if type(raw) is str:
        if len(raw.encode("utf-8")) > _MAX_BYTES:
            raise ShadowDenied("OVERSIZED_PAYLOAD")
        try:
            raw=json.loads(raw)
        except (ValueError, TypeError):
            raise ShadowDenied("UNPARSEABLE_PAYLOAD") from None
    if type(raw) is not dict:
        raise ShadowDenied("NON_OBJECT_PAYLOAD")
    result={}
    for key in ("endpoint", "suite_id"):
        if key in raw:
            result[key]=_text(raw[key],key)
    if "chain" in raw:
        chain=raw["chain"]
        if chain is not None and type(chain) is not dict:
            raise ShadowDenied("UNREVIEWED_CHAIN_TYPE")
        result["chain"]={} if chain is None else {
            k:_text(chain[k],k) for k in _CHAIN_KEYS if k in chain}
    if "fail_set" in raw:
        fails=raw["fail_set"]
        if fails is None:
            fails=[]
        if type(fails) is not list or len(fails)>_MAX_FAILS:
            raise ShadowDenied("UNREVIEWED_FAIL_SET_SIZE")
        reduced=[]
        for item in fails:
            if type(item) is not dict:
                raise ShadowDenied("UNREVIEWED_FAIL_ITEM_TYPE")
            reduced.append({k:_text(item[k],k) for k in _FAIL_KEYS if k in item})
        result["fail_set"]=reduced
    compact=json.dumps(result,separators=(",",":"),sort_keys=True,ensure_ascii=True)
    if len(compact.encode("utf-8")) > _MAX_BYTES:
        raise ShadowDenied("OVERSIZED_MINIMIZED_PAYLOAD")
    return compact

def minimize_task(row: dict) -> dict:
    from trace_b02_harness_projection_candidate import KEYS
    if type(row) is not dict or set(row)!=KEYS:
        raise ShadowDenied("UNREVIEWED_TASK_FIELDS")
    # The selected Task fields may themselves contain user-sensitive text.
    # Whitelisting payload keys does NOT establish PII/tenant authorization.
    return {**row, "payload_json":minimize_payload(row["payload_json"])}

def disposition() -> dict:
    return {"schema":"assistx-b02-shadow-policy-v1",
            "trusted_header_only_denied":True,
            "global_operator_scope_accepted":False,
            "tenant_isolation_proven":False,
            "payload_value_pii_review_complete":False,
            "staging_shadow_enabled":False,
            "production_route_authorized":False,
            "release_authorized":False}
