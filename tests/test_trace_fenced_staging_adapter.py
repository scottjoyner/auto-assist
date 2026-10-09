"""No-network staging adapter acceptance with synthetic Redis/query only."""
from __future__ import annotations

import asyncio
from threading import Event
import time

import pytest

from assistx import trace_fenced_staging_adapter as adapter
from assistx import trace_index_fenced_research as model

SECRET = "synthetic-receiver-key-do-not-use-live-000000000000"
FAST = adapter.StagingParameters(heartbeat_seconds=0.01, watchdog_join_seconds=0.2)


class FakeRedis:
    def __init__(self, *, acquire=(1, 0, 0), renew=1, release=1):
        self.acquire_result = acquire
        self.renew_result = renew
        self.release_result = release
        self.calls = []

    def eval(self, script, numkeys, *args):
        if script == model.ACQUIRE_LUA:
            self.calls.append("acquire")
            result = self.acquire_result
        elif script == model.RENEW_LUA:
            self.calls.append("renew")
            result = self.renew_result
        elif script == model.RELEASE_LUA:
            self.calls.append("release")
            result = self.release_result
        else:
            raise AssertionError("unrecognized Lua operation")
        if isinstance(result, Exception):
            raise result
        return result


def run(db, query=None, cancel=None, params=FAST):
    return adapter.run_staging_fenced_read(
        redis_client=db, principal="synthetic-authorized-operator", receiver_key=SECRET,
        query=query or (lambda cancelled: "safe-read"),
        request_cancel=cancel or (lambda: None),
        parameters=params,
    )


def test_accepted_read_releases_exact_nonce_and_returns_result():
    db = FakeRedis()
    assert run(db) == "safe-read"
    assert db.calls == ["acquire", "release"]


@pytest.mark.parametrize("reason", [(0, 1, 3), (0, 2, 5), (0, 3, 3), (0, 4, 1)])
def test_quota_or_slot_denials_never_query_or_attempt_release(reason):
    db = FakeRedis(acquire=reason)
    with pytest.raises(adapter.FencedReadDenied) as caught:
        run(db, query=lambda _: pytest.fail("denied query must not run"))
    assert db.calls == ["acquire"]
    assert caught.value.retry_after_seconds == reason[2]
    assert adapter.staging_http_disposition(caught.value) == (
        429, {"Retry-After": str(reason[2])})


def test_acquire_outage_fails_503_without_query():
    db = FakeRedis(acquire=ConnectionError("Redis down"))
    with pytest.raises(adapter.FencedReadUnavailable) as caught:
        run(db, query=lambda _: pytest.fail("unadmitted query ran"))
    assert db.calls == ["acquire"]
    assert adapter.staging_http_disposition(caught.value) == (503, {"Retry-After": "5"})


@pytest.mark.parametrize("release_result", [0, TimeoutError("lost release ack"), "bad"])
def test_unconfirmed_cleanup_must_not_return_successful_sensitive_result(release_result):
    db = FakeRedis(release=release_result)
    cancelled = []
    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, query=lambda _: "SYNTHETIC_SECRET_RESULT", cancel=lambda: cancelled.append(1))
    assert db.calls == ["acquire", "release"]
    assert cancelled == [1]


def test_query_exception_preserved_after_confirmed_release():
    db = FakeRedis()

    def query(_):
        raise ValueError("synthetic db failure")

    with pytest.raises(ValueError, match="synthetic db failure"):
        run(db, query=query)
    assert db.calls == ["acquire", "release"]


@pytest.mark.parametrize("renew_result", [0, TimeoutError("renew unavailable"), "bad"])
def test_renewal_loss_requests_cooperative_cancellation_and_denies_result(renew_result):
    db = FakeRedis(renew=renew_result)
    observed = []

    def query(cancel_event: Event):
        assert cancel_event.wait(timeout=0.3), "watchdog did not signal cancellation"
        observed.append("noticed")
        # Even if a cooperative query accidentally returns data, suppress it.
        return "SYNTHETIC_PRIVATE_RESULT"

    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, query=query, cancel=lambda: observed.append("cancel_called"))
    assert "renew" in db.calls and db.calls[-1] == "release"
    assert observed == ["cancel_called", "noticed"]


def test_successful_lease_renewal_then_release():
    db = FakeRedis()

    def query(cancelled):
        time.sleep(0.035)
        assert not cancelled.is_set()
        return {"synthetic": True}

    assert run(db, query=query) == {"synthetic": True}
    assert db.calls[0] == "acquire" and db.calls[-1] == "release"
    assert "renew" in db.calls


def test_query_that_does_not_honor_cancellation_cannot_return_response():
    db = FakeRedis(renew=0)

    def slow_legacy_callback(cancelled):
        time.sleep(0.04)
        return "DONT_SEND_ME"

    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, query=slow_legacy_callback)
    assert "renew" in db.calls


def test_failed_cancel_hook_does_not_turn_lost_authority_into_success():
    db = FakeRedis(renew=0)

    def broken_cancel():
        raise RuntimeError("driver does not support physical cancellation")

    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, query=lambda event: event.wait(.2), cancel=broken_cancel)
    assert db.calls[-1] == "release"


@pytest.mark.parametrize("parameters", [
    adapter.StagingParameters(heartbeat_seconds=-1),
    adapter.StagingParameters(heartbeat_seconds=8),
    adapter.StagingParameters(watchdog_join_seconds=0),
    adapter.StagingParameters(watchdog_join_seconds=30),
])
def test_invalid_staging_timing_never_orphans_a_lease(parameters):
    db = FakeRedis()
    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, params=parameters)
    # Invalid local types/bounds are rejected before acquisition; lease-specific
    # validation after admission must release the already-acquired slot.
    expected = ["acquire", "release"] if parameters.heartbeat_seconds == 8 else []
    assert db.calls == expected


def test_cancellation_protocol_is_required_before_quota_access():
    db = FakeRedis()
    with pytest.raises(adapter.FencedReadUnavailable):
        adapter.run_staging_fenced_read(
            redis_client=db, principal="fixture", receiver_key=SECRET,
            query=lambda token: "ok", request_cancel=None,
        )
    assert db.calls == []


def test_unknown_exception_is_not_misreported_as_rate_exhaustion():
    with pytest.raises(TypeError):
        adapter.staging_http_disposition(ValueError("unexpected"))


def test_module_is_not_wired_to_live_routes():
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "src/assistx/swarm_routes.py"
    assert "trace_fenced_staging_adapter" not in source.read_text(encoding="utf8")


def test_blocked_renewal_watchdog_must_not_authorize_a_result():
    """A simulated hung Redis renewal cannot be treated as a good lease."""
    from threading import Event as ThreadEvent

    renewal_started = ThreadEvent()
    allow_renew_finish = ThreadEvent()
    cancelled = []

    class HungRenewRedis(FakeRedis):
        def eval(self, script, count, *args):
            if script == model.RENEW_LUA:
                renewal_started.set()
                assert allow_renew_finish.wait(.5)
            return super().eval(script, count, *args)

    db = HungRenewRedis()
    params = adapter.StagingParameters(heartbeat_seconds=.005, watchdog_join_seconds=.02)

    def query(_):
        assert renewal_started.wait(.2)
        return "SYNTHETIC_NOT_TO_BE_RETURNED"

    try:
        with pytest.raises(adapter.FencedReadUnavailable):
            run(db, query=query, cancel=lambda: cancelled.append(1), params=params)
        assert cancelled, "hung watchdog must request cancellation"
    finally:
        allow_renew_finish.set()
        time.sleep(.02)  # let disposable daemon exit; no real Redis call


def test_query_failure_and_release_outage_preserve_fail_closed_disposition():
    db = FakeRedis(release=TimeoutError("redis unavailable on completion"))

    def query(_):
        raise ValueError("synthetic graph query timeout")

    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, query=query)
    assert db.calls == ["acquire", "release"]


@pytest.mark.parametrize("value", [None, "0.1", True, float("nan"), float("inf"), -1])
def test_invalid_heartbeat_type_or_value_is_rejected_before_acquire(value):
    db = FakeRedis()
    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, params=adapter.StagingParameters(heartbeat_seconds=value))
    assert db.calls == []


@pytest.mark.parametrize("value", [None, "3", False, float("nan"), float("-inf")])
def test_invalid_join_timeout_rejected_before_acquire(value):
    db = FakeRedis()
    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, params=adapter.StagingParameters(watchdog_join_seconds=value))
    assert db.calls == []


def test_watchdog_start_failure_attempts_exact_nonce_cleanup(monkeypatch):
    db = FakeRedis()
    monkeypatch.setattr(adapter.Thread, "start", lambda self: (_ for _ in ()).throw(
        RuntimeError("synthetic thread resources exhausted")
    ))
    with pytest.raises(adapter.FencedReadUnavailable, match="watchdog could not start"):
        run(db, query=lambda _: pytest.fail("must not start query"))
    assert db.calls == ["acquire", "release"]


def test_watchdog_start_and_cleanup_failure_both_fail_closed(monkeypatch):
    db = FakeRedis(release=TimeoutError("synthetic cleanup ack missing"))
    monkeypatch.setattr(adapter.Thread, "start", lambda self: (_ for _ in ()).throw(
        RuntimeError("watchdog start denied")
    ))
    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, query=lambda _: pytest.fail("query must not start"))
    assert db.calls == ["acquire", "release"]


def test_asyncio_cancelled_error_never_skips_owned_release():
    db = FakeRedis()
    with pytest.raises(asyncio.CancelledError):
        run(db, query=lambda _: (_ for _ in ()).throw(asyncio.CancelledError()))
    assert db.calls == ["acquire", "release"]


def test_asyncio_cancelled_error_after_lease_revocation_reports_unavailable():
    db = FakeRedis(renew=0)
    cancellation_requested = []
    def query(cancelled):
        assert cancelled.wait(.25)
        raise asyncio.CancelledError()
    with pytest.raises(adapter.FencedReadUnavailable):
        run(db, query=query, cancel=lambda: cancellation_requested.append(True))
    assert cancellation_requested == [True]
    assert db.calls[0] == "acquire" and db.calls[-1] == "release"
