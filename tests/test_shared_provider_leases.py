"""Offline acceptance of serialized provider admission and traceable decisions."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sqlite3
import threading

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/shared_provider_leases.py"
spec = importlib.util.spec_from_file_location("shared_provider_leases", SCRIPT)
leases = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = leases
spec.loader.exec_module(leases)


def test_h1_twenty_clients_one_slot(tmp_path):
    ledger = leases.ProviderLeaseLedger(tmp_path / "leases.sqlite")
    barrier = threading.Barrier(20)

    def contender(i):
        barrier.wait(timeout=10)
        return ledger.acquire("kilo", "client-" + str(i),
                              "request-key-" + str(i), now=1000,
                              ttl_seconds=90, input_reserved=100,
                              output_reserved=30)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(contender, range(20)))
    assert sum(r["granted"] for r in results) == 1
    assert ledger.snapshot(now=1000)["kilo"]["active"] == 1
    assert ledger.verify_audit() is True


def test_h2_idempotency_and_release(tmp_path):
    ledger = leases.ProviderLeaseLedger(tmp_path / "leases.sqlite")
    first = ledger.acquire("kilo_free", "hermes", "request-0001", now=1000,
                           input_reserved=200, output_reserved=30)
    assert first["granted"]
    again = ledger.acquire("kilo_anonymous", "hermes", "request-0001", now=1001,
                           input_reserved=200, output_reserved=30)
    assert again["lease_id"] == first["lease_id"]
    assert again["reason"] == "existing_active_lease"
    assert not ledger.acquire("kilo", "other", "request-0001", now=1002)["granted"]
    assert ledger.release(first["lease_id"], "other", now=1003)["released"] is False
    assert ledger.release(first["lease_id"], "hermes", now=1003)["released"] is True
    assert ledger.release(first["lease_id"], "hermes", now=1004)["reason"] == "already_released"
    assert not ledger.acquire("kilo", "hermes", "request-0001", now=1005,
                              input_reserved=200, output_reserved=30)["granted"]
    assert ledger.verify_audit()


def test_h3_scoped_rate_limit_and_auth_trip(tmp_path):
    policies = {"kilo": leases.Policy(), "cohere": leases.Policy()}
    aliases = {"kilo_free": "kilo", "cohere_direct": "cohere"}
    ledger = leases.ProviderLeaseLedger(tmp_path / "leases.sqlite",
                                        policies=policies, aliases=aliases)
    trip = ledger.trip("kilo_free", 429, now=1000)
    assert trip["blocked_until"] >= 4600
    assert not ledger.acquire("kilo_free", "hermes", "request-2001", now=1200)["granted"]
    other = ledger.acquire("cohere_direct", "other", "request-2002", now=1200)
    assert other["granted"]
    assert ledger.trip("kilo_free", 401, now=1300)["blocked_until"] >= 87700
    assert not ledger.renew(other["lease_id"], "wrong", now=1250)["renewed"]
    assert ledger.snapshot(now=1300)["kilo"]["circuit_open"] is True
    assert ledger.snapshot(now=1300)["cohere"]["circuit_open"] is False
    assert ledger.verify_audit()


def test_h4_expired_lease_cannot_renew_and_slot_recovers(tmp_path):
    ledger = leases.ProviderLeaseLedger(tmp_path / "leases.sqlite")
    first = ledger.acquire("kilo", "a", "request-3001", now=1000, ttl_seconds=10)
    assert first["granted"]
    assert ledger.renew(first["lease_id"], "a", now=1011)["renewed"] is False
    assert ledger.snapshot(now=1011)["kilo"]["active"] == 0
    next_lease = ledger.acquire("kilo", "b", "request-3002", now=1011)
    assert next_lease["granted"]


def test_token_and_request_windows(tmp_path):
    policy = leases.Policy(requests_per_window=2,
                           reserved_input_per_window=100,
                           reserved_output_per_window=30)
    ledger = leases.ProviderLeaseLedger(tmp_path / "leases.sqlite",
                                        policies={"kilo": policy})
    a = ledger.acquire("kilo", "a", "request-4001",
                       input_reserved=80, output_reserved=10, now=1000)
    assert a["granted"]
    assert ledger.release(a["lease_id"], "a", now=1001)["released"]
    b = ledger.acquire("kilo", "b", "request-4002",
                       input_reserved=21, output_reserved=10, now=1002)
    assert b["reason"] == "input_window_full"
    c = ledger.acquire("kilo", "c", "request-4003",
                       input_reserved=20, output_reserved=21, now=1003)
    assert c["reason"] == "output_window_full"
    d = ledger.acquire("kilo", "d", "request-4004",
                       input_reserved=20, output_reserved=20, now=1004)
    assert d["granted"]
    assert ledger.release(d["lease_id"], "d", now=1005)["released"]
    e = ledger.acquire("kilo", "e", "request-4005", now=1006)
    assert e["reason"] == "request_window_full"



def test_h5_chained_audit_detects_tampering(tmp_path):
    db = tmp_path / "leases.sqlite"
    ledger = leases.ProviderLeaseLedger(db)
    grant = ledger.acquire("kilo", "agent", "request-5001", now=1000)
    ledger.release(grant["lease_id"], "agent", now=1001)
    assert ledger.verify_audit()
    with sqlite3.connect(db) as con:
        con.execute("UPDATE audit SET reason='tampered' WHERE seq=1")
    assert not ledger.verify_audit()


def test_unknown_upstream_and_invalid_inputs_fail_closed(tmp_path):
    ledger = leases.ProviderLeaseLedger(tmp_path / "leases.sqlite")
    assert ledger.acquire("omniroute", "a", "request-6001")["reason"] == "unknown_or_ambiguous_provider"
    assert ledger.acquire("kilo", "a", "request-6001", input_reserved=0)["reason"] == "invalid_reservation"
    assert ledger.acquire("kilo", "a", "request-6001", ttl_seconds=999)["reason"] == "invalid_reservation"
    assert ledger.trip("kilo", 200)["reason"] == "not_a_trip_status"


def test_db_permissions_private(tmp_path):
    db = tmp_path / "leases.sqlite"
    leases.ProviderLeaseLedger(db)
    assert db.stat().st_mode & 0o077 == 0
    assert leases.ProviderLeaseLedger(db).verify_audit()


def _process_attempt(db, index, ready, queue):
    ready.wait(timeout=8)
    ledger = leases.ProviderLeaseLedger(Path(db))
    decision = ledger.acquire("kilo", f"worker-{index}",
                              f"process-request-{index}", now=1000,
                              input_reserved=100, output_reserved=10)
    queue.put(decision["granted"])


def test_cross_process_one_slot_serialization(tmp_path):
    from multiprocessing import get_context
    ctx = get_context("spawn")
    db = tmp_path / "shared.sqlite"
    leases.ProviderLeaseLedger(db)
    ready = ctx.Event()
    queue = ctx.Queue()
    workers = [ctx.Process(target=_process_attempt,
                           args=(str(db), i, ready, queue)) for i in range(8)]
    for worker in workers:
        worker.start()
    ready.set()
    grants = [queue.get(timeout=10) for _ in workers]
    for worker in workers:
        worker.join(timeout=10)
    assert all(worker.exitcode == 0 for worker in workers)
    assert sum(grants) == 1
    assert leases.ProviderLeaseLedger(db).verify_audit()


def test_retry_after_cannot_shorten_existing_circuit(tmp_path):
    ledger = leases.ProviderLeaseLedger(tmp_path / "shared.sqlite")
    first = ledger.trip("kilo", 503, now=1000, retry_after=7200)
    second = ledger.trip("kilo_free", 429, now=1100, retry_after=10)
    assert first["blocked_until"] == 8200
    assert second["blocked_until"] == 8200


def test_renewal_never_shortens_existing_lease(tmp_path):
    ledger = leases.ProviderLeaseLedger(tmp_path / "leases.sqlite")
    first = ledger.acquire("kilo", "agent", "request-7001", now=1000, ttl_seconds=120)
    assert first["granted"]
    renewed = ledger.renew(first["lease_id"], "agent", ttl_seconds=5, now=1001)
    assert renewed["renewed"] is True
    assert renewed["expires_at"] == 1120
    assert ledger.verify_audit()
