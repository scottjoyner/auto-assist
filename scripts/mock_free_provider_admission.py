#!/usr/bin/env python3
"""Offline mock-only provider admission. NOT a live router, signer, or issuer.

Uses the existing ProviderLeaseLedger acquire/renew/release/trip contract,
plus synthetic identity, epoch, source, and cost checks.
The only permitted executor is RecordingMockProvider. No HTTP calls.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

ALIASES = frozenset({"free", "auto", "router", "any", "best", "default"})


def _exact_model(model: str) -> bool:
    if not isinstance(model, str) or not model or model != model.strip():
        return False
    leaf = model.rsplit("/", 1)[-1].split(":", 1)[0].lower()
    return bool(leaf) and leaf not in ALIASES


def _zero(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return False
    return price.is_finite() and price == 0


@dataclass(frozen=True)
class RouteQualification:
    provider: str
    model: str
    resolved_model: str
    account_scope: str
    upstream_group: str
    proof_ref: str
    prompt_price: str
    completion_price: str
    verified: bool


@dataclass(frozen=True)
class DispatchRequest:
    provider: str
    model: str
    client: str
    node: str
    credential_ref: str
    request_key: str
    input_reserved: int = 100
    output_reserved: int = 100
    ttl_seconds: int = 30


@dataclass(frozen=True)
class MockResult:
    admitted: bool
    reason: str
    provider_calls: int
    upstream_group: str | None = None
    proof_ref: str | None = None


class MockHTTPError(Exception):
    def __init__(self, status_code: int, retry_after: int = 0):
        super().__init__(f"synthetic_http_{status_code}")
        self.status_code = status_code
        self.retry_after = retry_after


class MockLeaseCancelled(Exception):
    """Synthetic cancellation, raised before the first unauthorized step."""


class RecordingMockProvider:
    """Synthetic-only provider. Steps are bounded; no URLs or external tools."""
    def __init__(self, *, status_code: int | None = None,
                 reported_cost: str = "0", retry_after: int = 0,
                 steps: int = 1, step_seconds: int = 5):
        if type(steps) is not int or not 1 <= steps <= 16:
            raise ValueError("invalid_mock_steps")
        if type(step_seconds) is not int or not 0 <= step_seconds <= 120:
            raise ValueError("invalid_mock_step_interval")
        self.calls = 0
        self.status_code = status_code
        self.reported_cost = reported_cost
        self.retry_after = retry_after
        self.steps = steps
        self.step_seconds = step_seconds

    def invoke(self, *, heartbeat=None) -> str:
        # First step is guarded by the immediately preceding lease witness.
        # Every additional mock step must pass a fresh witnessed renewal.
        for i in range(self.steps):
            if i and (heartbeat is None or not heartbeat(i, i * self.step_seconds)):
                raise MockLeaseCancelled("synthetic_lease_lost")
            self.calls += 1
            if self.status_code is not None:
                raise MockHTTPError(self.status_code, self.retry_after)
        return self.reported_cost


class MockFreeProviderAdmission:
    """Mock-only contract: two external authorities must attest lease identity.

    ledger implements the existing SQLite pilot's acquire, renew,
    release, and trip. authority is a synthetic witness with:
       authenticate(client, node, credential_ref, account_scope) -> bool
       attest(lease_id, client, node, request_key, upstream_group, epoch) -> bool
       qualify(provider, requested, resolved, account, group, proof, prices, epoch) -> bool
    Qualification must come from a separate trusted fixture/authority, not
    from the route's self-declared group or cost fields. This is STILL
    synthetic, not a production trust root.
    """
    def __init__(self, ledger: Any, authority: Any,
                 routes: Mapping[tuple[str, str], RouteQualification],
                 *, authority_epoch: int, now: float):
        self.ledger = ledger
        self.authority = authority
        self.routes = dict(routes)
        self.epoch = authority_epoch
        self.now = float(now)
        self.quarantined: set[tuple[str, str]] = set()
        self.quarantined_groups: set[str] = set()
        self.cooldown_until: dict[tuple[str, str], float] = {}
        self.group_cooldown_until: dict[str, float] = {}

    def dispatch(self, request: DispatchRequest, provider: RecordingMockProvider) -> MockResult:
        before = provider.calls if type(provider) is RecordingMockProvider else 0

        def deny(reason: str, group: str | None = None, proof: str | None = None) -> MockResult:
            return MockResult(False, reason, 0, group, proof)

        if type(provider) is not RecordingMockProvider:
            return deny("mock_only")
        route = self.routes.get((request.provider, request.model))
        route_key = (request.provider, request.model)
        if route is None or route_key in self.quarantined:
            return deny("unqualified_or_quarantined")
        if isinstance(route, RouteQualification):
            if route.upstream_group in self.quarantined_groups:
                return deny("unqualified_or_quarantined")
            if (self.cooldown_until.get(route_key, 0) > self.now
                or self.group_cooldown_until.get(route.upstream_group, 0) > self.now):
                return deny("circuit_cooldown")
        if (not isinstance(route, RouteQualification)
            or route.provider != request.provider or route.model != request.model
            or not _exact_model(route.model) or not _exact_model(route.resolved_model)
            or not route.verified or not route.proof_ref
            or not route.upstream_group or not route.account_scope
            or not _zero(route.prompt_price) or not _zero(route.completion_price)):
            return deny("missing_exact_zero_cost_proof")
        if not request.client or not request.node or not request.credential_ref:
            return deny("missing_principal")
        try:
            witnessed = self.authority.qualify(
                route.provider, route.model, route.resolved_model,
                route.account_scope, route.upstream_group, route.proof_ref,
                route.prompt_price, route.completion_price, self.epoch
            )
            if witnessed is not True:
                return deny("unwitnessed_quota_proof")
        except Exception:
            return deny("qualification_authority_unavailable")
        try:
            if not self.authority.authenticate(request.client, request.node,
                                               request.credential_ref, route.account_scope):
                return deny("unauthenticated_client")
        except Exception:
            return deny("authority_unavailable")
        try:
            grant = self.ledger.acquire(
                request.provider, request.client, request.request_key,
                input_reserved=request.input_reserved,
                output_reserved=request.output_reserved,
                ttl_seconds=request.ttl_seconds, now=self.now
            )
        except Exception:
            return deny("lease_authority_unavailable")
        if (not isinstance(grant, dict) or not grant.get("granted")
            or grant.get("provider") != route.upstream_group
            or not isinstance(grant.get("lease_id"), str)
            or not grant.get("lease_id")
            or not isinstance(grant.get("expires_at"), (int, float))
            or grant["expires_at"] <= self.now):
            return deny("lease_denied", route.upstream_group, route.proof_ref)
        lease_id = grant["lease_id"]
        try:
            valid = self.authority.attest(
                lease_id, request.client, request.node, request.request_key,
                route.upstream_group, self.epoch
            )
            if not valid:
                return deny("unwitnessed_lease", route.upstream_group, route.proof_ref)
            renewal = self.ledger.renew(
                lease_id, request.client, ttl_seconds=request.ttl_seconds, now=self.now
            )
            if (not isinstance(renewal, dict) or not renewal.get("renewed")
                or not isinstance(renewal.get("expires_at"), (int, float))
                or renewal["expires_at"] <= self.now):
                return deny("renewal_denied", route.upstream_group, route.proof_ref)
            if not self.authority.attest(
                lease_id, request.client, request.node, request.request_key,
                route.upstream_group, self.epoch
            ):
                return deny("lease_witness_lost", route.upstream_group, route.proof_ref)
            def heartbeat(step: int, offset_seconds: int) -> bool:
                step_now = self.now + offset_seconds
                try:
                    renewed = self.ledger.renew(
                        lease_id, request.client,
                        ttl_seconds=request.ttl_seconds, now=step_now
                    )
                    if (not isinstance(renewed, dict)
                        or renewed.get("renewed") is not True
                        or not isinstance(renewed.get("expires_at"), (int, float))
                        or renewed["expires_at"] <= step_now):
                        return False
                    return self.authority.attest(
                        lease_id, request.client, request.node,
                        request.request_key, route.upstream_group,
                        self.epoch
                    ) is True
                except Exception:
                    return False

            try:
                reported_cost = provider.invoke(heartbeat=heartbeat)
            except MockLeaseCancelled:
                self.quarantined_groups.add(route.upstream_group)
                return MockResult(False, "mock_stream_cancelled", provider.calls - before,
                                  route.upstream_group, route.proof_ref)
            except MockHTTPError as error:
                if error.status_code in (401, 402, 403, 429, 503):
                    # Also hold locally if the shared trip endpoint is unavailable.
                    # The shared ledger remains authoritative across all clients.
                    safe_retry = error.retry_after if (
                        type(error.retry_after) is int and 0 <= error.retry_after <= 86400
                    ) else 0
                    delay = max(safe_retry, 86400 if error.status_code in (401, 402, 403) else 3600)
                    self.cooldown_until[route_key] = self.now + delay
                    self.group_cooldown_until[route.upstream_group] = max(
                        self.group_cooldown_until.get(route.upstream_group, 0),
                        self.now + delay
                    )
                    try:
                        self.ledger.trip(request.provider, error.status_code,
                                         retry_after=safe_retry, now=self.now)
                    except Exception:
                        # Explicit local backpressure; never blind-retry after partition.
                        self.quarantined_groups.add(route.upstream_group)
                return MockResult(False, "mock_upstream_denied", provider.calls - before,
                                  route.upstream_group, route.proof_ref)
            except Exception:
                self.quarantined_groups.add(route.upstream_group)
                return MockResult(False, "mock_execution_error", provider.calls - before,
                                  route.upstream_group, route.proof_ref)
            if not _zero(reported_cost):
                self.quarantined_groups.add(route.upstream_group)
                return MockResult(False, "nonzero_or_missing_usage_receipt",
                                  provider.calls - before, route.upstream_group, route.proof_ref)
            return MockResult(True, "mock_only_success", provider.calls - before,
                              route.upstream_group, route.proof_ref)
        except Exception:
            return deny("authority_or_execution_error", route.upstream_group, route.proof_ref)
        finally:
            try:
                self.ledger.release(lease_id, request.client, now=self.now)
            except Exception:
                pass
