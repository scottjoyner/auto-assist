"""Native SQLite resource-effect boundary research; disposable temp files only.

A BEFORE INSERT trigger checks a current-term permit and effect identity in
the SAME database transaction, unlike Python readback before effect insertion.
This is a same-admin thought experiment, NOT Neo4j RBAC or independent custody.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest


def connection(path):
    conn = sqlite3.connect(path, timeout=12, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=12000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def digest(s="synthetic-operation"):
    return hashlib.sha256(s.encode()).hexdigest()


def bootstrap(path):
    with connection(path) as db:
        db.executescript("""
            CREATE TABLE term_head (
                scope TEXT PRIMARY KEY, term INTEGER NOT NULL CHECK (term >= 1));
            INSERT INTO term_head VALUES ('context',1);

            CREATE TABLE permits (
                op_key TEXT PRIMARY KEY, payload_digest TEXT NOT NULL,
                term INTEGER NOT NULL, scope TEXT NOT NULL CHECK(scope='context'));
            CREATE TABLE effects (
                op_key TEXT PRIMARY KEY,
                payload_digest TEXT NOT NULL,
                term INTEGER NOT NULL,
                effect_id TEXT NOT NULL UNIQUE);
            CREATE TABLE receipts (
                op_key TEXT PRIMARY KEY REFERENCES effects(op_key),
                effect_id TEXT NOT NULL UNIQUE,
                payload_digest TEXT NOT NULL,
                term INTEGER NOT NULL,
                nonce TEXT NOT NULL UNIQUE);

            CREATE TRIGGER context_effect_guard BEFORE INSERT ON effects BEGIN
                SELECT RAISE(ABORT,'NO_PERMIT_OR_STALE_TERM')
                WHERE NOT EXISTS (
                    SELECT 1 FROM permits p
                    JOIN term_head h ON h.scope=p.scope
                    WHERE p.op_key=NEW.op_key
                      AND p.payload_digest=NEW.payload_digest
                      AND p.term=NEW.term
                      AND h.term=NEW.term
                      AND p.scope='context');
            END;
            CREATE TRIGGER context_effect_receipt AFTER INSERT ON effects BEGIN
                INSERT INTO receipts(op_key,effect_id,payload_digest,term,nonce)
                VALUES (NEW.op_key,NEW.effect_id,NEW.payload_digest,NEW.term,
                        lower(hex(randomblob(16))));
            END;
        """)


def permit(path, key, payload_hash, term=1):
    # Research-only DBA setup; NOT authenticated source authority.
    with connection(path) as db:
        db.execute("INSERT INTO permits VALUES (?,?,?,?)",
                   (key, payload_hash, term, "context"))


def write_effect(path, key, payload_hash, term=1):
    # The INSERT and trigger execute in a single local resource transaction.
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("INSERT INTO effects VALUES (?,?,?,?)",
                       (key, payload_hash, term, "effect:" + key))
            db.commit()
        except BaseException:
            db.rollback()
            raise


def readback(path, key):
    # One read-only connection to both actual resource row and commit-trigger
    # generated receipt. This is an inspection, NOT an attested COMMIT.
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
        row = db.execute(
            """SELECT e.op_key,e.payload_digest,e.term,e.effect_id,
                      r.op_key,r.payload_digest,r.term,r.effect_id,r.nonce
               FROM effects e
               LEFT JOIN receipts r ON r.op_key=e.op_key
               WHERE e.op_key=?""", (key,)).fetchone()
        if row is None:
            return "NO_EFFECT", None
        op, payload, term, effect, rop, rpayload, rterm, reffect, nonce = row
        if (op, payload, term, effect) != (rop, rpayload, rterm, reffect):
            return "UNCERTAIN_HOLD", None
        if not isinstance(nonce, str) or len(nonce) != 32:
            return "UNCERTAIN_HOLD", None
        return "MATCHED_UNATTESTED", nonce


@pytest.fixture
def resource(tmp_path):
    path = tmp_path / "disposable-effect-boundary.sqlite"
    bootstrap(path)
    return path


def test_valid_permit_and_effect_share_atomic_resource_commit(resource):
    permit(resource, "op:1", digest())
    write_effect(resource, "op:1", digest())
    state, nonce = readback(resource, "op:1")
    assert state == "MATCHED_UNATTESTED" and len(nonce) == 32


def test_missing_permit_direct_sql_insert_denied_by_trigger(resource):
    with pytest.raises(sqlite3.IntegrityError, match="NO_PERMIT"):
        write_effect(resource, "op:unsigned", digest())
    assert readback(resource, "op:unsigned")[0] == "NO_EFFECT"


def test_preflight_then_term_revoked_before_insert_is_denied(resource):
    permit(resource, "op:stale", digest())
    with connection(resource) as db:
        assert db.execute("SELECT term FROM term_head").fetchone() == (1,)
        db.execute("UPDATE term_head SET term=2 WHERE scope='context'")
    with pytest.raises(sqlite3.IntegrityError, match="STALE_TERM"):
        write_effect(resource, "op:stale", digest(), term=1)
    assert readback(resource, "op:stale")[0] == "NO_EFFECT"


@pytest.mark.parametrize("change", ["digest", "term", "op_key"])
def test_exact_effect_identity_checked_inside_insert(resource, change):
    permit(resource, "op:identity", digest(), term=1)
    values = {"key": "op:identity", "payload_hash": digest(), "term": 1}
    if change == "digest":
        values["payload_hash"] = digest("different")
    if change == "term":
        values["term"] = 2
    if change == "op_key":
        values["key"] = "op:other"
    with pytest.raises(sqlite3.IntegrityError, match="NO_PERMIT"):
        write_effect(resource, **values)
    assert readback(resource, values["key"])[0] == "NO_EFFECT"


def test_receipt_is_generated_in_same_transaction_not_before(resource):
    permit(resource, "op:receipt", digest())
    with connection(resource) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO effects VALUES (?,?,?,?)",
                   ("op:receipt", digest(), 1, "effect:op:receipt"))
        assert db.execute("SELECT count(*) FROM receipts").fetchone() == (1,)
        db.rollback()
    assert readback(resource, "op:receipt")[0] == "NO_EFFECT"
    with connection(resource) as db:
        assert db.execute("SELECT count(*) FROM receipts").fetchone() == (0,)


def test_two_concurrent_valid_writers_cannot_duplicate(resource):
    permit(resource, "op:race", digest())
    barrier = Barrier(6)

    def writer(_):
        barrier.wait(timeout=8)
        try:
            write_effect(resource, "op:race", digest())
        except sqlite3.IntegrityError:
            return "DUPLICATE_DENIED"
        return "COMMITTED"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(writer, range(6)))
    assert results.count("COMMITTED") == 1
    assert results.count("DUPLICATE_DENIED") == 5
    with connection(resource) as db:
        assert db.execute("SELECT count(*) FROM effects").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM receipts").fetchone() == (1,)


def test_lost_ack_then_retry_does_not_issue_second_effect(resource):
    permit(resource, "op:ack-lost", digest())
    write_effect(resource, "op:ack-lost", digest())
    prior = readback(resource, "op:ack-lost")
    with pytest.raises(sqlite3.IntegrityError):
        write_effect(resource, "op:ack-lost", digest())
    assert readback(resource, "op:ack-lost") == prior


def test_manual_privileged_trigger_removal_bypasses_permit_control(resource):
    with connection(resource) as db:
        db.executescript("""
            DROP TRIGGER context_effect_guard;
            INSERT INTO effects VALUES (
                'op:privileged-direct', 'falsified-payload', 1, 'effect:direct');
        """)
    assert readback(resource, "op:privileged-direct")[0] == "MATCHED_UNATTESTED"
    # Receipt exists because same-admin-controlled trigger signed nothing.
    # Therefore even a matching readback is NOT proof of authentic admission.


def test_same_admin_can_fabricate_permit_without_source_consent(resource):
    permit(resource, "op:forged-permit", digest("unauthorized"))
    write_effect(resource, "op:forged-permit", digest("unauthorized"))
    assert readback(resource, "op:forged-permit")[0] == "MATCHED_UNATTESTED"
    # No cryptographic source identity or privileged owner separation present.


def test_same_admin_restoring_entire_db_reenables_effect(resource, tmp_path):
    backup = tmp_path / "before-effect.sqlite"
    shutil.copy2(resource, backup)
    permit(resource, "op:restore", digest())
    write_effect(resource, "op:restore", digest())
    assert readback(resource, "op:restore")[0] == "MATCHED_UNATTESTED"
    shutil.copy2(backup, resource)
    assert readback(resource, "op:restore")[0] == "NO_EFFECT"
    permit(resource, "op:restore", digest())
    write_effect(resource, "op:restore", digest())
    assert readback(resource, "op:restore")[0] == "MATCHED_UNATTESTED"
    # P0 deliberate replay: one visible effect, TWO logical commits.


def test_missing_db_not_recreated_during_readback(resource):
    resource.unlink()
    with pytest.raises(sqlite3.OperationalError):
        readback(resource, "op:absent")
    assert not resource.exists()


def test_resource_readback_nonce_does_not_prevent_postread_change(resource):
    permit(resource, "op:readback", digest())
    write_effect(resource, "op:readback", digest())
    sample = readback(resource, "op:readback")
    assert sample[0] == "MATCHED_UNATTESTED"
    with connection(resource) as db:
        db.execute("DELETE FROM receipts WHERE op_key=?", ("op:readback",))
    assert readback(resource, "op:readback")[0] == "UNCERTAIN_HOLD"
    # Previously captured nonce remains a stale snapshot. Cannot grant effects.



def test_term_revocation_cannot_interleave_inside_effect_transaction(resource):
    """Single SQLite resource writer lock serializes revocation vs effect commit."""
    from threading import Event

    permit(resource, "op:serialized", digest())
    revocation_started = Event()

    def revoke_term():
        revocation_started.set()
        with connection(resource) as other:
            other.execute("BEGIN IMMEDIATE")
            other.execute("UPDATE term_head SET term=2 WHERE scope='context'")
            other.commit()
        return "REVOKED"

    # Hold the writer lock across effect+receipt mutation. A separate
    # connection cannot revoke term within that same resource transaction.
    with connection(resource) as owner:
        owner.execute("BEGIN IMMEDIATE")
        owner.execute("INSERT INTO effects VALUES (?,?,?,?)",
                      ("op:serialized", digest(), 1, "effect:serialized"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(revoke_term)
            assert revocation_started.wait(timeout=4)
            assert not future.done()
            owner.commit()
            assert future.result(timeout=8) == "REVOKED"
    assert readback(resource, "op:serialized")[0] == "MATCHED_UNATTESTED"
    with connection(resource) as db:
        assert db.execute("SELECT term FROM term_head").fetchone() == (2,)
    # A previously committed receipt exists, but is NOW stale by current
    # resource term. An external ACK still needs current effective fencing.


def test_privileged_owner_can_forge_matching_receipt_after_direct_write(resource):
    """P0: same-admin can disable guards and supply plausible receipt fields."""
    with connection(resource) as db:
        db.executescript("""
            DROP TRIGGER context_effect_guard;
            DROP TRIGGER context_effect_receipt;
            INSERT INTO effects VALUES (
                'op:forged-receipt', 'arbitrary', 1, 'effect:forged');
            INSERT INTO receipts VALUES (
                'op:forged-receipt', 'effect:forged', 'arbitrary', 1,
                '00000000000000000000000000000000');
        """)
    assert readback(resource, "op:forged-receipt") == (
        "MATCHED_UNATTESTED", "0" * 32)
    # A joined database receipt is NOT equivalent to independent resource
    # ownership or a cryptographically attested, nonbypassable effect.


def test_different_effect_keys_get_distinct_transaction_nonces(resource):
    for k in ("op:a", "op:b", "op:c"):
        permit(resource, k, digest())
        write_effect(resource, k, digest())
    nonces = [readback(resource, k)[1] for k in ("op:a", "op:b", "op:c")]
    assert len(nonces) == len(set(nonces)) == 3
    assert all(isinstance(n, str) and len(n) == 32 for n in nonces)
    # Unique random nonces do NOT make same-admin rollback impossible.