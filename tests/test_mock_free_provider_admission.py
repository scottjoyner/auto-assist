"""Mock-only acceptance for knowledge issue #57. No network or real providers."""
import importlib.util
from pathlib import Path
import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/mock_free_provider_admission.py"
spec = importlib.util.spec_from_file_location("mock_free_provider_admission", MODULE)
import sys
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class SyntheticLedger:
    def __init__(self):
        self.calls = []
        self.acquires = 0
        self.grant = True
        self.expires_at = 1100.0
        self.group = "shared-upstream"
        self.writable = True
        self.renewal = True
        self.trip_codes = []
        self.released = []

    def acquire(self, provider, client, request_key, **kwargs):
        self.calls.append(("acquire", provider, client, request_key))
        if not self.writable:
            raise OSError("partition")
        self.acquires += 1
        return {"granted": self.grant, "provider": self.group,
                "lease_id": "synthetic-lease-1", "expires_at": self.expires_at}

    def renew(self, lease_id, client, **kwargs):
        self.calls.append(("renew", lease_id, client))
        if not self.writable:
            raise OSError("partition")
        return {"renewed": self.renewal, "expires_at": self.expires_at}

    def release(self, lease_id, client, **kwargs):
        self.released.append((lease_id, client))
        return {"released": True}

    def trip(self, provider, status_code, *, retry_after, now):
        self.trip_codes.append((provider, status_code, retry_after))
        return {"tripped": True}


class SyntheticIdentity:
    def __init__(self):
        self.connected = True
        self.epoch = 9
        self.principals = {("agent", "xwing", "fixture-credential", "account-A")}
        self.attest_calls = 0
        self.revoke_after_one = False
        self.revoke_after_n = None
        self.accepted_routes = {
            ("kilo_free", "cohere/north-mini-code:free",
             "cohere/north-mini-code:free", "account-A", "shared-upstream",
             "synthetic-proof-20261009", "0", "0.000")
        }
        self.qualification_connected = True
        self.qualification_calls = 0

    def qualify(self, provider, requested, resolved, account, group,
                proof_ref, prompt_price, completion_price, epoch):
        self.qualification_calls += 1
        if not self.qualification_connected:
            raise ConnectionError("synthetic qualification partition")
        return (epoch == self.epoch and
                (provider, requested, resolved, account, group,
                 proof_ref, prompt_price, completion_price) in self.accepted_routes)

    def authenticate(self, client, node, credential_ref, account_scope):
        if not self.connected:
            raise ConnectionError("synthetic partition")
        return (client, node, credential_ref, account_scope) in self.principals

    def attest(self, lease_id, client, node, request_key, group, epoch):
        if not self.connected:
            raise ConnectionError("synthetic partition")
        self.attest_calls += 1
        return (
            epoch == self.epoch and lease_id == "synthetic-lease-1"
            and client == "agent" and node == "xwing"
            and request_key == "mock-request-0001" and group == "shared-upstream"
            and not (self.revoke_after_one and self.attest_calls > 1)
            and not (self.revoke_after_n is not None and
                     self.attest_calls > self.revoke_after_n)
        )


@pytest.fixture
def setting():
    ledger = SyntheticLedger()
    authority = SyntheticIdentity()
    qual = m.RouteQualification(
        provider="kilo_free", model="cohere/north-mini-code:free",
        resolved_model="cohere/north-mini-code:free",
        account_scope="account-A", upstream_group="shared-upstream",
        proof_ref="synthetic-proof-20261009",
        prompt_price="0", completion_price="0.000", verified=True,
    )
    request = m.DispatchRequest(
        provider=qual.provider, model=qual.model, client="agent", node="xwing",
        credential_ref="fixture-credential", request_key="mock-request-0001"
    )
    gate = m.MockFreeProviderAdmission(
        ledger, authority, {(qual.provider, qual.model): qual},
        authority_epoch=9, now=1000
    )
    return gate, ledger, authority, qual, request


def test_positive_synthetic_lease_requires_double_witness_and_zero_usage(setting):
    gate, ledger, auth, qual, request = setting
    result = gate.dispatch(request, m.RecordingMockProvider())
    assert result.admitted and result.provider_calls == 1
    assert result.upstream_group == "shared-upstream"
    assert result.proof_ref == qual.proof_ref
    assert auth.attest_calls == 2
    assert ledger.released == [("synthetic-lease-1", "agent")]
    assert [x[0] for x in ledger.calls] == ["acquire", "renew"]


@pytest.mark.parametrize("bad", [
    {"prompt_price": "0.0000001"}, {"completion_price": "-0.01"},
    {"prompt_price": "nan"}, {"completion_price": "inf"},
    {"prompt_price": None}, {"verified": False},
    {"proof_ref": ""}, {"account_scope": ""}, {"upstream_group": ""},
    {"model": "free", "resolved_model": "free"},
    {"resolved_model": "openrouter/auto"},
    {"resolved_model": ""},
])
def test_unqualified_or_cost_unsafe_route_has_zero_calls(setting, bad):
    gate, ledger, _, qual, request = setting
    data = dict(vars(qual)); data.update(bad)
    updated = m.RouteQualification(**data)
    if "model" in bad:
        request = m.DispatchRequest(**{**vars(request), "model": updated.model})
    gate.routes = {(request.provider, request.model): updated}
    provider = m.RecordingMockProvider()
    assert not gate.dispatch(request, provider).admitted
    assert provider.calls == ledger.acquires == 0


@pytest.mark.parametrize("field,value", [
    ("provider", "openrouter"), ("model", "openrouter/free"),
    ("client", "stranger"), ("node", "x1-370"),
    ("credential_ref", "forged"), ("request_key", "replayed-key"),
])
def test_wrong_route_identity_node_or_replay_never_invokes_provider(setting, field, value):
    gate, ledger, _, _, request = setting
    request = m.DispatchRequest(**{**vars(request), field: value})
    provider = m.RecordingMockProvider()
    assert not gate.dispatch(request, provider).admitted
    assert provider.calls == 0


def test_nonmock_provider_is_never_invoked(setting):
    gate, ledger, _, _, request = setting
    class DangerousCallable:
        calls = 0
        def invoke(self):
            self.calls += 1
            raise AssertionError("must not execute")
    provider = DangerousCallable()
    result = gate.dispatch(request, provider)
    assert not result.admitted and result.reason == "mock_only"
    assert provider.calls == ledger.acquires == 0


@pytest.mark.parametrize("mode", ["authority_partition", "ledger_partition", "deny",
                                  "expired", "wrong_group", "renewal_rejected",
                                  "epoch_rotation", "revoke_before_dispatch"])
def test_negative_admission_is_zero_provider_calls(setting, mode):
    gate, ledger, auth, qual, request = setting
    if mode == "authority_partition": auth.connected = False
    if mode == "ledger_partition": ledger.writable = False
    if mode == "deny": ledger.grant = False
    if mode == "expired": ledger.expires_at = gate.now
    if mode == "wrong_group": ledger.group = "unrelated-quota"
    if mode == "renewal_rejected": ledger.renewal = False
    if mode == "epoch_rotation": auth.epoch += 1
    if mode == "revoke_before_dispatch": auth.revoke_after_one = True
    provider = m.RecordingMockProvider()
    result = gate.dispatch(request, provider)
    assert not result.admitted, mode
    assert provider.calls == result.provider_calls == 0, mode


@pytest.mark.parametrize("http_code", [401, 402, 403, 429, 503])
def test_upstream_mock_failure_trips_shared_lease_group(setting, http_code):
    gate, ledger, _, _, request = setting
    mock = m.RecordingMockProvider(status_code=http_code, retry_after=75)
    result = gate.dispatch(request, mock)
    assert not result.admitted
    assert result.reason == "mock_upstream_denied"
    assert result.provider_calls == mock.calls == 1
    assert ledger.trip_codes == [("kilo_free", http_code, 75)]
    assert ledger.released == [("synthetic-lease-1", "agent")]


@pytest.mark.parametrize("reported", ["0.00001", "-0.01", "NaN", "Infinity", None])
def test_metering_mismatch_quarantines_future_attempts(setting, reported):
    gate, ledger, _, _, request = setting
    first = m.RecordingMockProvider(reported_cost=reported)
    verdict = gate.dispatch(request, first)
    assert not verdict.admitted and verdict.reason == "nonzero_or_missing_usage_receipt"
    again = m.RecordingMockProvider()
    result = gate.dispatch(request, again)
    assert result.reason == "unqualified_or_quarantined"
    assert again.calls == 0
    assert ledger.acquires == 1


def test_missing_provider_credential_ref_does_not_reach_ledger(setting):
    gate, ledger, _, _, request = setting
    request = m.DispatchRequest(**{**vars(request), "credential_ref": ""})
    mock = m.RecordingMockProvider()
    assert gate.dispatch(request, mock).reason == "missing_principal"
    assert mock.calls == ledger.acquires == 0


def test_invalid_bool_zero_prices_are_not_accepted(setting):
    gate, ledger, _, qual, request = setting
    gate.routes = {(qual.provider, qual.model): m.RouteQualification(
        **{**vars(qual), "completion_price": False})}
    assert gate.dispatch(request, m.RecordingMockProvider()).reason == "missing_exact_zero_cost_proof"
    assert ledger.acquires == 0


@pytest.mark.parametrize("http_code,delay", [
    (403, 86400), (429, 3600), (503, 3600)
])
def test_provider_error_cools_down_next_request_without_retry(setting, http_code, delay):
    gate, ledger, auth, qual, request = setting
    fail = m.RecordingMockProvider(status_code=http_code)
    assert not gate.dispatch(request, fail).admitted
    assert gate.cooldown_until[(request.provider, request.model)] == gate.now + delay
    gate.now += 10
    again = m.RecordingMockProvider()
    result = gate.dispatch(request, again)
    assert result.reason == "circuit_cooldown"
    assert again.calls == 0
    assert ledger.acquires == 1


def test_failed_circuit_report_quarantines_route_and_no_retry(setting):
    gate, ledger, auth, qual, request = setting
    def interrupted(*args, **kwargs):
        raise ConnectionError("synthetic circuit authority partition")
    ledger.trip = interrupted
    assert not gate.dispatch(request, m.RecordingMockProvider(status_code=503)).admitted
    again = m.RecordingMockProvider()
    assert gate.dispatch(request, again).reason == "unqualified_or_quarantined"
    assert again.calls == 0


def test_unexpected_synthetic_execution_error_preserves_metering_and_blocks_retry(setting):
    gate, ledger, auth, qual, request = setting
    class BrokenReceipt(m.RecordingMockProvider):
        pass
    mock = m.RecordingMockProvider()
    mock.reported_cost = object()
    result = gate.dispatch(request, mock)
    assert result.reason == "nonzero_or_missing_usage_receipt"
    assert result.provider_calls == 1
    again = m.RecordingMockProvider()
    assert gate.dispatch(request, again).reason == "unqualified_or_quarantined"
    assert again.calls == 0



@pytest.mark.parametrize("field,value", [
    ("proof_ref", "synthetic-forged-proof"),
    ("upstream_group", "invented-extra-free-quota"),
    ("account_scope", "another-account"),
    ("resolved_model", "cohere/north-mini-code:alternate"),
    ("prompt_price", "0.00"),
    ("completion_price", "0"),
])
def test_route_cannot_self_attest_quota_or_zero_cost_provenance(setting, field, value):
    gate, ledger, auth, qual, request = setting
    changed = m.RouteQualification(**{**vars(qual), field: value})
    gate.routes = {(changed.provider, changed.model): changed}
    provider = m.RecordingMockProvider()
    result = gate.dispatch(request, provider)
    assert result.reason == "unwitnessed_quota_proof"
    assert provider.calls == ledger.acquires == 0


def test_qualification_authority_partition_denies_before_lease_or_mock_dispatch(setting):
    gate, ledger, auth, qual, request = setting
    auth.qualification_connected = False
    provider = m.RecordingMockProvider()
    result = gate.dispatch(request, provider)
    assert result.reason == "qualification_authority_unavailable"
    assert provider.calls == ledger.acquires == 0


def test_qualification_epoch_rotation_does_not_dispatch(setting):
    gate, ledger, auth, qual, request = setting
    auth.epoch += 1
    provider = m.RecordingMockProvider()
    result = gate.dispatch(request, provider)
    assert result.reason == "unwitnessed_quota_proof"
    assert provider.calls == ledger.acquires == 0


def test_same_upstream_second_alias_cannot_skip_first_route_429_cooldown(setting):
    gate, ledger, auth, qual, request = setting
    other = m.RouteQualification(
        provider="openrouter", model="thinkingmachines/inkling-small:free",
        resolved_model="thinkingmachines/inkling-small:free",
        account_scope="account-A", upstream_group="shared-upstream",
        proof_ref="fixture-openrouter-shared-upstream",
        prompt_price="0", completion_price="0", verified=True
    )
    gate.routes[(other.provider, other.model)] = other
    auth.accepted_routes.add((
        other.provider, other.model, other.resolved_model,
        other.account_scope, other.upstream_group, other.proof_ref,
        other.prompt_price, other.completion_price
    ))
    first = m.RecordingMockProvider(status_code=429)
    result1 = gate.dispatch(request, first)
    assert result1.reason == "mock_upstream_denied"
    assert first.calls == 1
    second_request = m.DispatchRequest(
        provider=other.provider, model=other.model, client="agent", node="xwing",
        credential_ref="fixture-credential", request_key="mock-request-0002"
    )
    second = m.RecordingMockProvider()
    result2 = gate.dispatch(second_request, second)
    assert result2.reason == "circuit_cooldown"
    assert second.calls == 0
    assert ledger.acquires == 1


def test_same_upstream_second_alias_quarantined_on_lost_circuit_witness(setting):
    gate, ledger, auth, qual, request = setting
    other = m.RouteQualification(
        provider="openrouter", model="thinkingmachines/inkling-small:free",
        resolved_model="thinkingmachines/inkling-small:free",
        account_scope="account-A", upstream_group="shared-upstream",
        proof_ref="fixture-openrouter-shared-upstream",
        prompt_price="0", completion_price="0", verified=True
    )
    gate.routes[(other.provider, other.model)] = other
    auth.accepted_routes.add((
        other.provider, other.model, other.resolved_model, other.account_scope,
        other.upstream_group, other.proof_ref, other.prompt_price, other.completion_price
    ))
    def broken(*args, **kwargs):
        raise ConnectionError("synthetic shared circuit unavailable")
    ledger.trip = broken
    first = m.RecordingMockProvider(status_code=503)
    assert gate.dispatch(request, first).reason == "mock_upstream_denied"
    second_request = m.DispatchRequest(
        provider=other.provider, model=other.model, client="agent", node="xwing",
        credential_ref="fixture-credential", request_key="mock-request-0002"
    )
    second = m.RecordingMockProvider()
    assert gate.dispatch(second_request, second).reason == "unqualified_or_quarantined"
    assert second.calls == 0
    assert ledger.acquires == 1



def test_mock_multi_step_stream_requires_witness_at_each_subsequent_step(setting):
    gate, ledger, authority, _, request = setting
    mock = m.RecordingMockProvider(steps=4, step_seconds=5)
    result = gate.dispatch(request, mock)
    assert result.admitted and result.reason == "mock_only_success"
    assert result.provider_calls == mock.calls == 4
    assert authority.attest_calls == 5  # two initial + three heartbeat witnesses
    assert [x[0] for x in ledger.calls] == ["acquire"] + ["renew"] * 4
    assert ledger.released == [("synthetic-lease-1", "agent")]


def test_stream_stops_after_one_step_when_lease_witness_revoked(setting):
    gate, ledger, authority, _, request = setting
    authority.revoke_after_n = 2
    mock = m.RecordingMockProvider(steps=4)
    result = gate.dispatch(request, mock)
    assert not result.admitted and result.reason == "mock_stream_cancelled"
    assert result.provider_calls == mock.calls == 1
    assert len(ledger.released) == 1
    next_attempt = m.RecordingMockProvider()
    assert gate.dispatch(request, next_attempt).reason == "unqualified_or_quarantined"
    assert next_attempt.calls == 0


def test_mock_stream_denies_next_chunk_when_lease_expires(setting):
    gate, ledger, _, _, request = setting
    mock = m.RecordingMockProvider(steps=4, step_seconds=120)
    result = gate.dispatch(request, mock)
    assert result.reason == "mock_stream_cancelled"
    assert result.provider_calls == mock.calls == 1
    assert len(ledger.released) == 1


def test_mock_stream_denies_next_chunk_on_lease_authority_partition(setting):
    gate, ledger, authority, _, request = setting
    original = ledger.renew
    calls = 0
    def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise ConnectionError("synthetic lease coordinator disappeared")
        return original(*args, **kwargs)
    ledger.renew = flaky
    mock = m.RecordingMockProvider(steps=4)
    result = gate.dispatch(request, mock)
    assert result.reason == "mock_stream_cancelled"
    assert result.provider_calls == mock.calls == 1


@pytest.mark.parametrize("steps,interval", [
    (0, 5), (17, 5), (True, 5), (1, -1), (1, 121), (1, 3.5)
])
def test_bounded_stream_workload_validation(steps, interval):
    with pytest.raises(ValueError):
        m.RecordingMockProvider(steps=steps, step_seconds=interval)


def test_no_mock_stream_without_heartbeat_after_first_step():
    mock = m.RecordingMockProvider(steps=3)
    with pytest.raises(m.MockLeaseCancelled):
        mock.invoke()
    assert mock.calls == 1


def test_post_execution_release_denial_downgrades_success_and_quarantines(setting):
    gate, ledger, _, route, request = setting
    def reject_release(*args, **kwargs):
        return {"released": False, "reason": "synthetic_wrong_owner"}
    ledger.release = reject_release
    mock = m.RecordingMockProvider(steps=2)
    result = gate.dispatch(request, mock)
    assert not result.admitted and result.reason == "release_unverified"
    assert result.provider_calls == mock.calls == 2
    assert route.upstream_group in gate.quarantined_groups
    after = m.RecordingMockProvider()
    assert gate.dispatch(request, after).reason == "unqualified_or_quarantined"
    assert after.calls == 0


def test_post_execution_release_partition_downgrades_success_and_quarantines(setting):
    gate, ledger, _, route, request = setting
    def partition(*args, **kwargs):
        raise ConnectionError("synthetic_release_authority_partition")
    ledger.release = partition
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert not result.admitted and result.reason == "release_unavailable"
    assert result.provider_calls == mock.calls == 1
    assert route.upstream_group in gate.quarantined_groups
    assert gate.dispatch(request, m.RecordingMockProvider()).reason == "unqualified_or_quarantined"


def test_mock_release_malformed_acknowledgment_fails_closed(setting):
    gate, ledger, _, route, request = setting
    ledger.release = lambda *args, **kwargs: {"released": "yes"}
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert not result.admitted and result.reason == "release_unverified"
    assert result.provider_calls == 1
    assert route.upstream_group in gate.quarantined_groups


@pytest.mark.parametrize("field,value", [
    ("input_reserved", True), ("input_reserved", 0),
    ("input_reserved", -1), ("input_reserved", 1000001),
    ("output_reserved", False), ("output_reserved", 0),
    ("output_reserved", 1000001),
    ("ttl_seconds", True), ("ttl_seconds", 4),
    ("ttl_seconds", 121), ("ttl_seconds", 30.0),
])
def test_malformed_reservations_never_touch_ledger_or_provider(setting, field, value):
    gate, ledger, _, _, request = setting
    changed = m.DispatchRequest(**{**vars(request), field: value})
    mock = m.RecordingMockProvider()
    result = gate.dispatch(changed, mock)
    assert result.reason == "invalid_reservation"
    assert mock.calls == ledger.acquires == 0


@pytest.mark.parametrize("field,value", [
    ("client", 42), ("client", "x" * 129),
    ("node", True), ("node", "x" * 129),
    ("credential_ref", 55), ("credential_ref", "x" * 257),
    ("request_key", 77), ("request_key", "short"),
    ("request_key", "x" * 129),
])
def test_invalid_identity_types_and_request_keys_deny_before_lease(setting, field, value):
    gate, ledger, _, _, request = setting
    changed = m.DispatchRequest(**{**vars(request), field: value})
    mock = m.RecordingMockProvider()
    result = gate.dispatch(changed, mock)
    assert result.reason == "invalid_principal_or_request_key"
    assert mock.calls == ledger.acquires == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_authority_clock_denies_before_lease(setting, value):
    gate, ledger, _, _, request = setting
    gate.now = value
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "invalid_clock"
    assert mock.calls == ledger.acquires == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
                                     True, "1100", None])
def test_nonfinite_or_non_numeric_lease_expiry_denies_before_provider(setting, value):
    gate, ledger, _, _, request = setting
    ledger.expires_at = value
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "lease_denied"
    assert mock.calls == 0


@pytest.mark.parametrize("value", [False, 0, 1, "yes", None])
def test_lease_grant_requires_boolean_true(setting, value):
    gate, ledger, _, _, request = setting
    ledger.grant = value
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "lease_denied"
    assert mock.calls == 0


@pytest.mark.parametrize("value", [1, "true", None])
def test_route_verified_must_be_literal_boolean_true(setting, value):
    gate, ledger, _, route, request = setting
    changed = m.RouteQualification(**{**vars(route), "verified": value})
    gate.routes[(route.provider, route.model)] = changed
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "missing_exact_zero_cost_proof"
    assert mock.calls == ledger.acquires == 0


@pytest.mark.parametrize("value", [1, "true", None])
def test_authentication_requires_literal_true_before_lease(setting, value):
    gate, ledger, authority, _, request = setting
    authority.authenticate = lambda *args: value
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "unauthenticated_client"
    assert mock.calls == ledger.acquires == 0


@pytest.mark.parametrize("value", [1, "true", None])
def test_first_lease_witness_requires_literal_true(setting, value):
    gate, ledger, authority, _, request = setting
    authority.attest = lambda *args: value
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "unwitnessed_lease"
    assert mock.calls == 0
    assert len(ledger.released) == 1


@pytest.mark.parametrize("value", [1, "true", None])
def test_renewed_lease_requires_literal_true(setting, value):
    gate, ledger, _, _, request = setting
    original = ledger.renew
    def fake(*args, **kwargs):
        response = original(*args, **kwargs)
        return {**response, "renewed": value}
    ledger.renew = fake
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "renewal_denied"
    assert mock.calls == 0
    assert len(ledger.released) == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
                                     "1100", True, None])
def test_renewal_requires_finite_numeric_expiry(setting, value):
    gate, ledger, _, _, request = setting
    ledger.renew = lambda *args, **kwargs: {"renewed": True, "expires_at": value}
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "renewal_denied"
    assert mock.calls == 0
    assert len(ledger.released) == 1


def test_stream_heartbeat_rejects_nonfinite_or_nonboolean_renewal(setting):
    gate, ledger, _, _, request = setting
    n = 0
    def partial(*args, **kwargs):
        nonlocal n
        n += 1
        return ({"renewed": True, "expires_at": 1100}
                if n == 1 else {"renewed": "true", "expires_at": float("nan")})
    ledger.renew = partial
    mock = m.RecordingMockProvider(steps=4)
    result = gate.dispatch(request, mock)
    assert result.reason == "mock_stream_cancelled"
    assert result.provider_calls == 1
    assert len(ledger.released) == 1


def test_acquire_partition_after_possible_commit_quarantines_shared_group(setting):
    gate, ledger, _, route, request = setting
    def commit_then_partition(*args, **kwargs):
        ledger.acquires += 1
        raise ConnectionError("synthetic_connection_dropped_after_commit")
    ledger.acquire = commit_then_partition
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "lease_authority_unavailable"
    assert mock.calls == 0
    assert route.upstream_group in gate.quarantined_groups
    second = m.RecordingMockProvider()
    assert gate.dispatch(request, second).reason == "unqualified_or_quarantined"
    assert second.calls == 0


def test_malformed_positive_grant_quarantines_possible_lease(setting):
    gate, ledger, _, route, request = setting
    def broken_grant(*args, **kwargs):
        ledger.acquires += 1
        return {"granted": True, "lease_id": None, "provider": route.upstream_group,
                "expires_at": float("nan")}
    ledger.acquire = broken_grant
    result = gate.dispatch(request, m.RecordingMockProvider())
    assert result.reason == "lease_denied"
    assert route.upstream_group in gate.quarantined_groups
    next_mock = m.RecordingMockProvider()
    assert gate.dispatch(request, next_mock).reason == "unqualified_or_quarantined"
    assert next_mock.calls == 0


def test_expiry_huge_integer_is_rejected_without_exception(setting):
    gate, ledger, _, route, request = setting
    ledger.expires_at = 10 ** 1500
    mock = m.RecordingMockProvider()
    result = gate.dispatch(request, mock)
    assert result.reason == "lease_denied"
    assert mock.calls == 0
    assert route.upstream_group in gate.quarantined_groups


def test_normal_deny_does_not_quarantine_legitimate_quota_retry(setting):
    gate, ledger, _, route, request = setting
    ledger.grant = False
    first = m.RecordingMockProvider()
    assert gate.dispatch(request, first).reason == "lease_denied"
    assert first.calls == 0
    assert route.upstream_group not in gate.quarantined_groups
    ledger.grant = True
    next_mock = m.RecordingMockProvider()
    assert gate.dispatch(request, next_mock).admitted
    assert next_mock.calls == 1
