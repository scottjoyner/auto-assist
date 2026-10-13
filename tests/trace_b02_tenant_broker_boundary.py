"""B02 tenant broker boundary — OFFLINE CONTRACT ONLY; not a deployed API.
Only a server-owned principal->tenant mapping may select query tenant scope.
This module never instantiates Neo4j, routes requests, acquires keys or authorizes
production reads. A caller can *not* select their own tenant, even when Basic
credentials are otherwise verified.

ServerOwnedDirectory is a test fixture, not an authenticated issuer or a real
principal directory. The owning deployment must independently attest it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fastapi.security import HTTPBasicCredentials
from assistx.trace_preview_access import basic_preview_permitted
from trace_b02_harness_projection_candidate import (
    CYPHER_TENANT_SCOPED, TENANT_PLAN_ID, tenant_scoped_params,
    ProjectionDenied,
)


class BrokerBoundaryDenied(ValueError):
    pass


@dataclass(frozen=True)
class ServerOwnedDirectory:
    """Offline fixture: inject ONLY from trusted broker-side configuration."""
    revision: str
    principal_tenants: Mapping[str, str]
    signed_by_independent_authority: bool = False
    production_issuer_verified: bool = False

    def __post_init__(self):
        if not isinstance(self.revision, str) or not self.revision:
            raise BrokerBoundaryDenied("DIRECTORY_REVISION_REQUIRED")
        if (not isinstance(self.principal_tenants, Mapping)
                or not self.principal_tenants):
            raise BrokerBoundaryDenied("NO_SERVER_BOUND_TENANTS")


@dataclass(frozen=True)
class ReadIntent:
    """Research-only query intent: not proof of runtime broker enforcement."""
    plan_id: str
    cypher: str
    parameters: dict
    principal: str
    directory_revision: str
    principal_binding_source: str = "synthetic_test_directory"
    production_execution_authorized: bool = False
    graph_network_cutover_authorized: bool = False
    release_authorized: bool = False


def resolve_scoped_read(
    *,
    principal: str,
    credentials: HTTPBasicCredentials | None,
    configured_user: str | None,
    configured_password: str | None,
    allowed_basic_operators: str,
    server_owned_directory: ServerOwnedDirectory,
    request: Mapping,
) -> ReadIntent:
    """Returns a *pure* fixed plan. A user tenant field is always rejected."""
    if not isinstance(request, Mapping):
        raise BrokerBoundaryDenied("UNREVIEWED_BROKER_REQUEST")
    if set(request) != {"plan_id", "status"}:
        # Reject even a syntactically valid or matching tenant_id; the caller
        # must not choose/query scope. No raw Cypher, filter or limit input.
        raise BrokerBoundaryDenied("UNREVIEWED_CLIENT_SCOPE_OR_QUERY_FIELD")
    if request["plan_id"] != TENANT_PLAN_ID:
        raise BrokerBoundaryDenied("UNREVIEWED_BROKER_PLAN")
    if not basic_preview_permitted(
        principal, credentials,
        configured_user=configured_user,
        configured_password=configured_password,
        allowed_users=allowed_basic_operators,
    ):
        raise BrokerBoundaryDenied("VERIFIED_BASIC_OPERATOR_REQUIRED")
    if type(server_owned_directory) is not ServerOwnedDirectory:
        raise BrokerBoundaryDenied("UNTRUSTED_PRINCIPAL_DIRECTORY")
    # This source is not yet independently controlled or production-signed.
    if (server_owned_directory.signed_by_independent_authority
            or server_owned_directory.production_issuer_verified):
        raise BrokerBoundaryDenied("FAKE_FIXTURE_CANNOT_CLAIM_PRODUCTION_AUTHORITY")
    tenant = server_owned_directory.principal_tenants.get(principal)
    if type(tenant) is not str:
        raise BrokerBoundaryDenied("MISSING_AUTHORITATIVE_TENANT_BINDING")
    try:
        scoped = tenant_scoped_params(
            request["status"], trusted_tenant_from_server=tenant)
    except ProjectionDenied:
        raise BrokerBoundaryDenied("INVALID_BROKER_TENANT_OR_STATUS") from None
    return ReadIntent(
        plan_id=TENANT_PLAN_ID, cypher=CYPHER_TENANT_SCOPED,
        parameters=scoped, principal=principal,
        directory_revision=server_owned_directory.revision,
    )


def release_decision() -> dict:
    return {
        "schema": "assistx-b02-tenant-broker-boundary-research-v1",
        "production_principal_directory_attested": False,
        "principal_to_tenant_ownership_accepted": False,
        "existing_task_tenant_field_parity_accepted": False,
        "broker_network_exclusivity_accepted": False,
        "production_execution_authorized": False,
        "global_operator_read_authorized": False,
        "staging_shadow_authorized": False,
        "release_authorized": False,
    }
