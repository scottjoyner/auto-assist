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
