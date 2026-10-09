"""Synthetic physical-slot quarantine tests: NO production Redis/Neo4j."""
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
import pytest

from assistx.trace_index_quarantine import (
    TraceIndexQuarantine, QUARANTINE_ACQUIRE_LUA,
    QUARANTINE_RELEASE_LUA, QUARANTINE_INSPECT_LUA,
)


class FakeRedis:
    def __init__(self):
        self.lock = RLock()
        self.entries = {}
        self.now_ms = 1_000
        self.error = None
        self.override = None
        self.override_set = False

    def eval(self, script, numkeys, key, *args):
        assert numkeys == 1 and key == TraceIndexQuarantine.key
        with self.lock:
            if self.error:
                raise self.error
            if self.override_set:
                return self.override
            rows = self.entries.setdefault(key, {})
            if script == QUARANTINE_ACQUIRE_LUA:
                cap, token = args
                if len(rows) >= cap or token in rows:
                    return [0, len(rows)]
                rows[token] = self.now_ms
                return [1, len(rows)]
            if script == QUARANTINE_RELEASE_LUA:
                token = args[0]
                existed = token in rows
                rows.pop(token, None)
                return int(existed)
            if script == QUARANTINE_INSPECT_LUA:
                return len(rows)
            raise AssertionError("Unexpected Lua script")


def test_cap_one_old_query_cannot_be_replaced_after_30_seconds():
    store = FakeRedis()
    guard = TraceIndexQuarantine(store, slots=1)
    first, occupancy = guard.acquire()
    assert first and occupancy == 1
    physically_active = {first}
    store.now_ms += 30_001
    second, occupancy = guard.acquire()
    assert second is None and occupancy == 1
    assert guard.inspect_count() == 1
    assert len(physically_active) == 1
    assert guard.acknowledge_complete(first) is False
    assert guard.inspect_count() == 1
    assert guard.acknowledge_complete(first, remote_query_termination_verified=True)
    physically_active.remove(first)
    assert guard.acquire()[0] is not None


def test_long_clock_advance_never_evacuates_uncertain_slot():
    store = FakeRedis()
    guard = TraceIndexQuarantine(store, slots=1)
    old, _ = guard.acquire()
    store.now_ms += 7 * 24 * 60 * 60 * 1000
    assert guard.acquire()[0] is None
    assert old in store.entries[guard.key]


def test_worker_crash_causes_permanent_denial_until_explicit_reconciliation():
    store = FakeRedis()
    guard = TraceIndexQuarantine(store, slots=1)
    token, _ = guard.acquire()
    # The worker has vanished and has no verified cancel receipt.
    assert guard.acquire()[0] is None
    assert guard.acknowledge_complete(token, remote_query_termination_verified=False) is False
    assert guard.inspect_count() == 1


def test_wrong_or_stale_token_never_frees_an_active_slot():
    store = FakeRedis()
    guard = TraceIndexQuarantine(store, slots=1)
    token, _ = guard.acquire()
    assert guard.acknowledge_complete("a" * 32, remote_query_termination_verified=True) is False
    assert guard.inspect_count() == 1
    assert guard.acknowledge_complete(token, remote_query_termination_verified=True)
    assert guard.acknowledge_complete(token, remote_query_termination_verified=True) is False
    newest, _ = guard.acquire()
    assert newest != token
    assert guard.inspect_count() == 1


def test_capacity_across_concurrent_simulated_workers():
    store = FakeRedis()
    guard = TraceIndexQuarantine(store, slots=3)
    with ThreadPoolExecutor(max_workers=16) as pool:
        answers = list(pool.map(lambda _: guard.acquire()[0], range(35)))
    tokens = [t for t in answers if t]
    assert len(tokens) == 3 and len(set(tokens)) == 3
    assert guard.inspect_count() == 3


def test_occupancy_released_only_after_asserted_completion():
    store = FakeRedis()
    guard = TraceIndexQuarantine(store, slots=3)
    leases = [guard.acquire()[0] for _ in range(3)]
    assert guard.acquire()[0] is None
    assert not guard.acknowledge_complete(leases[0])
    assert guard.acknowledge_complete(leases[0], remote_query_termination_verified=True)
    assert guard.inspect_count() == 2
    assert guard.acquire()[0] not in set(leases)


def test_manual_redis_reset_can_overadmit_physically_active_queries():
    """Explicit falsification: Redis durability/reset can defeat this proposal."""
    store = FakeRedis()
    guard = TraceIndexQuarantine(store, slots=1)
    old, _ = guard.acquire()
    physically_active = {old}
    # Redis FLUSH, failover losing state, or split brain: must remain NO-GO.
    store.entries.clear()
    new, _ = guard.acquire()
    physically_active.add(new)
    assert len(physically_active) == 2
    assert guard.inspect_count() == 1
    assert not guard.acknowledge_complete(old, remote_query_termination_verified=True)
    assert new in store.entries[guard.key]


@pytest.mark.parametrize("slots", [0,17,True,2.0,-1,"3"])
def test_invalid_capacity_rejected(slots):
    with pytest.raises(ValueError):
        TraceIndexQuarantine(FakeRedis(), slots=slots)


def test_missing_redis_client_rejected():
    with pytest.raises(ValueError):
        TraceIndexQuarantine(None)


@pytest.mark.parametrize("bad", [None, 0, 1, "ok", [1], [1, "1"], [1, -1], [2,1], [True,1]])
def test_malformed_acquire_denied(bad):
    store = FakeRedis()
    store.override = bad
    store.override_set = True
    assert TraceIndexQuarantine(store, slots=1).acquire()[0] is None


def test_redis_error_denies_and_release_cannot_be_trusted():
    store = FakeRedis()
    guard = TraceIndexQuarantine(store, slots=1)
    token, _ = guard.acquire()
    store.error = ConnectionError("synthetic connection loss")
    assert guard.acquire() == (None, None)
    assert guard.inspect_count() is None
    assert not guard.acknowledge_complete(token, remote_query_termination_verified=True)


@pytest.mark.parametrize("invalid", ["", "Z" * 32, "abcd", None, 9, True])
def test_invalid_release_token_rejected(invalid):
    store = FakeRedis()
    guard = TraceIndexQuarantine(store, slots=1)
    token, _ = guard.acquire()
    assert not guard.acknowledge_complete(invalid, remote_query_termination_verified=True)
    assert token in store.entries[guard.key]


def test_no_runtime_automatic_reap_or_key_expiry_in_prototype():
    assert "ZREMRANGEBYSCORE" not in QUARANTINE_ACQUIRE_LUA
    assert "PEXPIRE" not in QUARANTINE_ACQUIRE_LUA
    assert "EXPIRE" not in QUARANTINE_ACQUIRE_LUA
    assert "DEL" not in QUARANTINE_ACQUIRE_LUA
    assert "HDEL" not in QUARANTINE_ACQUIRE_LUA



def test_disconnected_redis_probe_guard_accepts_only_exact_test_target(monkeypatch):
    import json
    from types import SimpleNamespace
    import probe_trace_index_quarantine_redis as probe
    obj = {
        "Name": "/" + probe.NAME,
        "State": {"Running": True},
        "Config": {"Image": "redis:7-alpine"},
        "HostConfig": {
            "NetworkMode": "none", "PortBindings": {},
            "NanoCpus": 1000000000, "Memory": 128*1024*1024,
        },
        "Mounts": [],
    }
    def run(argv, **_):
        assert argv == ["docker","inspect",probe.NAME]
        return SimpleNamespace(returncode=0, stdout=json.dumps([obj]),stderr="")
    monkeypatch.setattr(probe.subprocess,"run",run)
    probe.check_target()


@pytest.mark.parametrize("change", [
    ("Name","/assistx-redis"),
    ("Config",{"Image":"redis:7"}),
    ("State",{"Running":False}),
    ("HostConfig",{"NetworkMode":"bridge"}),
    ("HostConfig",{"PortBindings":{"6379":[{"HostPort":"6379"}]}}),
    ("HostConfig",{"NanoCpus":2000000000}),
    ("HostConfig",{"Memory":1024*1024*1024}),
    ("Mounts",[{"Type":"bind","Source":"/nas","Destination":"/data"}]),
])
def test_disconnected_redis_probe_guard_rejects_unsafe(monkeypatch,change):
    import json
    from types import SimpleNamespace
    import probe_trace_index_quarantine_redis as probe
    obj = {
        "Name": "/" + probe.NAME,
        "State": {"Running": True},
        "Config": {"Image": "redis:7-alpine"},
        "HostConfig": {
            "NetworkMode": "none", "PortBindings": {},
            "NanoCpus": 1000000000, "Memory": 128*1024*1024,
        },
        "Mounts": [],
    }
    k,v=change
    if k in ("Config","State","HostConfig"):obj[k].update(v)
    else:obj[k]=v
    monkeypatch.setattr(probe.subprocess,"run",lambda *a,**kw: SimpleNamespace(
        returncode=0,stdout=json.dumps([obj]),stderr=""
    ))
    with pytest.raises(RuntimeError,match="REFUSING_UNSAFE_REDIS_TARGET"):
        probe.check_target()
