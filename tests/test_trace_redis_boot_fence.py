"""Synthetic Redis reboot / failover negative controls; no real Redis or graph."""
from __future__ import annotations

from threading import Event
import time

import pytest

from assistx.trace_fenced_staging_adapter import (
    FencedReadUnavailable,
    StagingParameters,
    run_staging_fenced_read,
)
from assistx.trace_index_fenced_research import ACQUIRE_LUA, RELEASE_LUA, RENEW_LUA

RUN_A = "a" * 40
RUN_B = "b" * 40
KEY = "synthetic-receiver-owned-custody-key-not-production-000000"
PARAMS = StagingParameters(heartbeat_seconds=0.01, watchdog_join_seconds=0.2)


class SyntheticRedis:
    def __init__(self):
        self.run_id = RUN_A
        self.calls = []
        self.after_acquire = False
        self.after_release = False
        self.renew_restart = False
        self.raise_info = False

    def info(self, section="server"):
        self.calls.append("info")
        if self.raise_info:
            raise ConnectionError("synthetic Redis failover")
        return {"run_id": self.run_id} if section == "server" else {}

    def eval(self, lua, keys, *args):
        if lua == ACQUIRE_LUA:
            self.calls.append("acquire")
            if self.after_acquire:
                self.run_id = RUN_B
            return [1, 0, 0]
        if lua == RENEW_LUA:
            self.calls.append("renew")
            if self.renew_restart:
                self.run_id = RUN_B
            return 1
        if lua == RELEASE_LUA:
            self.calls.append("release")
            if self.after_release:
                self.run_id = RUN_B
            return 1
        raise AssertionError("unknown Lua script")


def run(db, *, query=None, cancelled=None, pin=RUN_A):
    return run_staging_fenced_read(
        redis_client=db,
        principal="synthetic-operator",
        receiver_key=KEY,
        query=query or (lambda event: "synthetic-sensitive-result"),
        request_cancel=cancelled or (lambda: None),
        redis_run_id_pin=pin,
        parameters=PARAMS,
    )


@pytest.mark.parametrize("pin", [RUN_B, "", "bad", 123])
def test_invalid_or_mismatched_pin_never_admits(pin):
    db = SyntheticRedis()
    with pytest.raises(FencedReadUnavailable):
        run(db, pin=pin, query=lambda _: pytest.fail("query started"))
    assert "acquire" not in db.calls


def test_redis_info_outage_fails_before_acquisition():
    db = SyntheticRedis()
    db.raise_info = True
    with pytest.raises(FencedReadUnavailable):
        run(db, query=lambda _: pytest.fail("query started"))
    assert "acquire" not in db.calls


def test_valid_pin_and_unchanged_server_return_synthetic_result():
    db = SyntheticRedis()
    assert run(db) == "synthetic-sensitive-result"
    assert db.calls.count("info") >= 3
    assert db.calls.count("acquire") == 1
    assert db.calls.count("release") == 1


def test_restart_between_info_and_acquire_denies_before_query():
    db = SyntheticRedis()
    db.after_acquire = True
    with pytest.raises(FencedReadUnavailable):
        run(db, query=lambda _: pytest.fail("query started"))
    assert db.calls.count("acquire") == 1
    assert db.calls.count("release") == 1


def test_restart_during_read_is_not_a_success():
    db = SyntheticRedis()
    cancelled = []
    def slow_query(stop: Event):
        assert stop.wait(.3)
        return "synthetic-must-not-escape"

    def cancel():
        cancelled.append(True)

    # The heartbeat observes the changed instance identity and must deny.
    db.renew_restart = True
    with pytest.raises(FencedReadUnavailable):
        run(db, query=slow_query, cancelled=cancel)
    assert cancelled
    assert "renew" in db.calls


def test_redis_restart_during_release_denies_result():
    db = SyntheticRedis()
    db.after_release = True
    with pytest.raises(FencedReadUnavailable):
        run(db, query=lambda _: "synthetic-must-not-escape")
    assert "release" in db.calls


def test_redis_restart_before_release_denies_result():
    db = SyntheticRedis()
    def query(_):
        db.run_id = RUN_B
        return "synthetic-secret-response"
    with pytest.raises(FencedReadUnavailable):
        run(db, query=query)
    assert "release" in db.calls


def test_legacy_unpinned_research_contract_unchanged():
    db = SyntheticRedis()
    assert run(db, pin=None) == "synthetic-sensitive-result"
    assert "info" not in db.calls


def test_infrastructure_cannot_claim_run_id_pin_as_cancellation_proof():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/assistx/trace_fenced_staging_adapter.py").read_text()
    assert "NOT imported by FastAPI" in source
    assert "Redis boot identity" in source
