"""B02 metadata-only tenant read proposal. Synthetic OFFLINE research ONLY.

A fixed query returns no free text, payload_json, or failure data even to the
broker. A server-side principal-to-tenant fixture binding is not production
authentication. No route, Bolt connection, network operation, or graph effects.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
from fastapi.security import HTTPBasicCredentials
from assistx.trace_preview_access import basic_preview_permitted
from assistx.harness_views import HARNESS_TASK_KINDS
from trace_b02_harness_projection_candidate import (
    KEYS, params, validate_broker_rows, ProjectionDenied)

class TenantDenied(ValueError):
    pass

@dataclass(frozen=True)
class TenantContext:
    principal: str
    tenant_id: str
    mode: str = "synthetic_offline_only"

PLAN_ID = "harness_evolution_tasks_tenant_metadata_v0"
PROJECTION_KEYS = frozenset({
    "tenant_id","id","kind","status","target_agent_id","updated_at_ts"})
TENANT_CYPHER = (
    "MATCH (t:Task {tenant_id: $tenant_id, status: $status}) "
    "RETURN t { .tenant_id, .id, .kind, .status, "
    ".target_agent_id, .updated_at_ts } AS task "
    "ORDER BY t.created_at_ts DESC LIMIT $limit"
)
def _id(value: object, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise TenantDenied("UNREVIEWED_METADATA_IDENTIFIER")
    return value

def _tenant_id(value: object) -> str:
    result = _id(value)
    if len(result) > 64:
        raise TenantDenied("UNREVIEWED_TENANT_LENGTH")
    return result

def authorize_fixture(*, principal: str, credentials: HTTPBasicCredentials,
                      configured_user: str, configured_password: str,
                      tenant_bindings: dict, explicit_offline: bool) -> TenantContext:
    """Synthetic verified Basic -> server-side tenant map, never headers."""
    if explicit_offline is not True or type(tenant_bindings) is not dict:
        raise TenantDenied("OFFLINE_TENANT_POLICY_REQUIRED")
    if type(principal) is not str or principal not in tenant_bindings:
        raise TenantDenied("NO_VERIFIED_TENANT_BINDING")
    if not basic_preview_permitted(
            principal, credentials, configured_user=configured_user,
            configured_password=configured_password, allowed_users=principal):
        raise TenantDenied("VERIFIED_BASIC_PRINCIPAL_REQUIRED")
    return TenantContext(principal, _tenant_id(tenant_bindings[principal]))

def tenant_params(scope: TenantContext, *, status: str, limit: int = 40) -> dict:
    if type(scope) is not TenantContext or scope.mode != "synthetic_offline_only":
        raise TenantDenied("UNVERIFIED_SCOPE_CONTEXT")
    return {**params(status, limit), "tenant_id": _tenant_id(scope.tenant_id)}
def safe_tenant_rows(scope: TenantContext, rows: list[dict],
                     *, expected_status: str = "READY") -> list[dict]:
    """Reject entire result for any wrong/missing scope, status or projection.

    The query never fetches title/summary/error/payload_json. Those legacy
    response fields are explicitly None; view parity is intentionally lost.
    """
    query = tenant_params(scope, status=expected_status)
    if type(rows) is not list or len(rows) > 40:
        raise TenantDenied("INVALID_RESULT_SET")
    result = []
    for row in rows:
        if type(row) is not dict or set(row) != PROJECTION_KEYS:
            raise TenantDenied("UNSCOPED_OR_UNEXPECTED_ROW_FIELDS")
        if type(row["tenant_id"]) is not str or row["tenant_id"] != query["tenant_id"]:
            raise TenantDenied("CROSS_TENANT_OR_UNSCOPED_ROW")
        if row["status"] != query["status"]:
            raise TenantDenied("WRONG_STATUS_ROW")
        _id(row["id"])
        _id(row["target_agent_id"], optional=True)
        if row["kind"] not in HARNESS_TASK_KINDS:
            raise TenantDenied("UNREVIEWED_TASK_KIND")
        safe = {k:row.get(k) for k in KEYS}
        try:
            validate_broker_rows([safe])
        except ProjectionDenied:
            raise TenantDenied("UNREVIEWED_TASK_ROW") from None
        result.append(safe)
    return result

def disposition() -> dict:
    return dict(schema="assistx-b02-tenant-metadata-research-v0",
                plan=PLAN_ID, fixed_query_tenant_predicate=True,
                raw_payload_and_free_text_not_queried=True,
                synthetic_principal_binding_only=True,
                full_view_parity_proven=False,
                real_tenant_source_attested=False,
                graph_network_exclusivity_proven=False,
                production_route_authorized=False, release_authorized=False)