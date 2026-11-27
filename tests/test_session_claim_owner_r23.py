"""Offline contract tests for the optional caller-supplied claim owner (r23).

No production deployment, no live DB, no credentials. Two fixture shapes:

* shared-cache in-memory SQLite (production ``connect`` routed through a facade);
* a real temp SQLite file under pytest's ``tmp_path`` (with TMPDIR on the SSD).

Invariants checked: default claims are unchanged; a supplied session id is
recorded and is the release fence; quarantine-prefixed and malformed owners are
rejected before any write; the R11/R12/R13 guard logic is still present; and two
distinct owners cannot double-claim the same row. These are in-process invariant
checks, not capacity measurements or evidence about a live queue.
"""

import ast
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import pytest

import assistx.outbox_client as oc

SESSION_A = "sess-" + "a" * 32
SESSION_B = "sess-" + "b" * 32
PAYLOAD = '{"note":"R23_SENTINEL_NEVER_PRINTED"}'
OLD_CLAIMED_AT = "2026-11-20T00:00:00+00:00"


class InMemorySqlite:
    """sqlite3 facade: route production connect() calls to a shared memory DB."""

    Error = sqlite3.Error

    @staticmethod
    def connect(database, *args, **kwargs):
        kwargs["uri"] = True
        return sqlite3.connect(database, *args, **kwargs)


@pytest.fixture
def mem_outbox(monkeypatch):
    uri = f"file:claim-owner-r23-{uuid.uuid4().hex}?mode=memory&cache=shared"
    monkeypatch.setattr(oc, "sqlite3", InMemorySqlite)
    keeper = sqlite3.connect(uri, uri=True)
    try:
        oc.OutboxClient(db_path=uri, api_url="", auto_flush=False)
        yield uri, keeper
    finally:
        keeper.close()


@pytest.fixture
def file_outbox(tmp_path):
    """Real on-disk SQLite under pytest's temp base (TMPDIR is set to the SSD)."""
    path = str(tmp_path / f"outbox-r23-{uuid.uuid4().hex}.sqlite")
    client = oc.OutboxClient(db_path=path, api_url="", auto_flush=False)
    return path, client


def _seed(keeper, outbox_id="row-1", *, status="pending", owner=None, claimed_at=None):
    keeper.execute(
        "INSERT INTO outbox (outbox_id, event_id, payload_json, status, created_at, claim_owner, claimed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (outbox_id, "e-" + outbox_id, PAYLOAD, status, "2026-11-27T00:00:00+00:00", owner, claimed_at),
    )
    keeper.commit()


def _claim_with_retry(client, owner, deadline):
    """Bounded retry for shared-cache 'locked' races in the 2-thread test."""
    while time.monotonic() < deadline:
        try:
            return client._claim_next(owner=owner)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.005)
    return None


def test_default_claim_is_unchanged_file_sqlite(file_outbox):
    """With no owner, the uuid4-hex token and fencing semantics are byte-for-byte old behavior."""
    path, client = file_outbox
    for i in range(2):
        client.enqueue({"event_id": f"r23-default-{i}"})

    first = client._claim_next()
    second = client._claim_next()
    assert first is not None and second is not None
    for entry in (first, second):
        assert len(entry.claim_owner) == 32
        int(entry.claim_owner, 16)  # a fresh uuid4().hex, not a caller label
    assert first.claim_owner != second.claim_owner

    keeper = sqlite3.connect(path)
    try:
        state = {r[0]: r[1] for r in keeper.execute("SELECT outbox_id, claim_owner FROM outbox")}
    finally:
        keeper.close()
    assert state[first.outbox_id] == first.claim_owner
    assert state[second.outbox_id] == second.claim_owner
    assert client._claim_next() is None

    print(
        "R23_DEFAULT " + json.dumps({"tokens": 2, "hex32": True, "distinct": first.claim_owner != second.claim_owner})
    )


def test_supplied_session_id_is_recorded(mem_outbox):
    """A caller-supplied session id is written to claim_owner and returned to that caller."""
    uri, keeper = mem_outbox
    _seed(keeper)

    client = oc.OutboxClient(db_path=uri, api_url="", auto_flush=False)
    entry = client._claim_next(owner=SESSION_A)
    assert entry is not None
    assert entry.status == "claimed"
    assert entry.claim_owner == SESSION_A
    assert keeper.execute(
        "SELECT status, claim_owner FROM outbox WHERE outbox_id=?", (entry.outbox_id,)
    ).fetchone() == ("claimed", SESSION_A)
    # A quarantine hold written by an operator is still excluded from claims.
    assert client._claim_next(owner=SESSION_A) is None
    _seed(keeper, outbox_id="held", owner="quarantine:r23-hold", claimed_at=OLD_CLAIMED_AT)
    assert client._claim_next(owner=SESSION_A) is None


@pytest.mark.parametrize(
    "owner",
    [
        "quarantine:r23-hold",
        "QUARANTINE:hold",
        "QuArAnTiNe:Hold",
        "quarantine:",
    ],
)
def test_quarantine_prefixed_owner_rejected(mem_outbox, owner):
    """A caller cannot spoof an operator quarantine hold, in any ASCII case."""
    uri, keeper = mem_outbox
    _seed(keeper)

    client = oc.OutboxClient(db_path=uri, api_url="", auto_flush=False)
    with pytest.raises(ValueError, match="quarantine"):
        client._claim_next(owner=owner)

    # No write happened: the row is still pending and unclaimed.
    assert keeper.execute("SELECT status, claim_owner FROM outbox WHERE outbox_id='row-1'").fetchone() == (
        "pending",
        None,
    )
    # The row remains claimable by an ordinary (token-minting) claim.
    assert client._claim_next() is not None


@pytest.mark.parametrize(
    "owner",
    [
        "",
        "x" * (oc.MAX_CLAIM_OWNER_LEN + 1),
        "sess-\nnewline",
        "sess-\ttab",
        "sess-\x00nul",
        " sess-trailing ",
        123,
    ],
)
def test_owner_validation_guardrails(mem_outbox, owner):
    """Owners must be short, printable, non-blank strings with no control chars."""
    uri, keeper = mem_outbox
    _seed(keeper)

    client = oc.OutboxClient(db_path=uri, api_url="", auto_flush=False)
    with pytest.raises(ValueError):
        client._claim_next(owner=owner)
    assert keeper.execute("SELECT status, claim_owner FROM outbox WHERE outbox_id='row-1'").fetchone() == (
        "pending",
        None,
    )


def test_release_and_fencing_with_supplied_owner(mem_outbox):
    """The supplied owner is the fence: a wrong owner cannot release the row."""
    uri, keeper = mem_outbox
    _seed(keeper)

    client = oc.OutboxClient(db_path=uri, api_url="", auto_flush=False)
    entry = client._claim_next(owner=SESSION_A)
    assert entry.claim_owner == SESSION_A

    forged = oc.OutboxEntry(
        outbox_id=entry.outbox_id,
        event_id=entry.event_id,
        payload_json=entry.payload_json,
        status="claimed",
        claim_owner=SESSION_B,
    )
    assert client._release(forged, "delivered", 1) is False
    assert keeper.execute(
        "SELECT status, claim_owner FROM outbox WHERE outbox_id=?", (entry.outbox_id,)
    ).fetchone() == ("claimed", SESSION_A)

    # Release with the exact owner succeeds and clears the claim columns.
    assert client._release(entry, "delivered", 1) is True
    assert keeper.execute(
        "SELECT status, claim_owner, claimed_at FROM outbox WHERE outbox_id=?", (entry.outbox_id,)
    ).fetchone() == ("delivered", None, None)
    assert client._claim_next(owner=SESSION_B) is None  # terminal row is not deliverable

    print("R23_FENCE " + json.dumps({"forged_owner_released": False, "true_owner_released": True}))


def test_two_owners_never_double_claim(mem_outbox):
    """Two distinct owners cannot both hold a live row; an expired lease is recovered once."""
    uri, keeper = mem_outbox
    _seed(keeper)

    client_a = oc.OutboxClient(db_path=uri, api_url="", auto_flush=False)
    client_b = oc.OutboxClient(db_path=uri, api_url="", auto_flush=False)

    first = client_a._claim_next(owner=SESSION_A)
    assert first is not None and first.claim_owner == SESSION_A
    assert client_b._claim_next(owner=SESSION_B) is None, "a second owner double-claimed a live row"

    # Force the lease to expire, then the second owner may recover exactly once.
    keeper.execute("UPDATE outbox SET claimed_at=? WHERE outbox_id=?", (OLD_CLAIMED_AT, first.outbox_id))
    keeper.commit()
    recovered = client_b._claim_next(owner=SESSION_B)
    assert recovered is not None and recovered.claim_owner == SESSION_B
    assert client_a._claim_next(owner=SESSION_A) is None
    assert keeper.execute("SELECT claim_owner FROM outbox WHERE outbox_id=?", (first.outbox_id,)).fetchone() == (
        SESSION_B,
    )

    # The stale owner's release is fenced out and cannot clobber the recovered claim.
    assert client_a._release(first, "delivered", 1) is False
    assert keeper.execute(
        "SELECT status, claim_owner FROM outbox WHERE outbox_id=?", (first.outbox_id,)
    ).fetchone() == ("claimed", SESSION_B)
    assert client_b._release(recovered, "delivered", 1) is True


def test_two_concurrent_owners_claim_one_row_once(mem_outbox):
    """A bounded 2-thread race with distinct owners yields exactly one claim."""
    uri, keeper = mem_outbox
    _seed(keeper)

    clients = [oc.OutboxClient(db_path=uri, api_url="", auto_flush=False) for _ in range(2)]
    barrier = threading.Barrier(2)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait(timeout=5)
        entry = _claim_with_retry(clients[index], SESSION_A if index == 0 else SESSION_B, time.monotonic() + 5)
        with lock:
            results.append(entry)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), "claimant did not finish within the bounded deadline"

    winners = [e for e in results if e is not None]
    assert len(winners) == 1, "two owners both claimed the same row"
    assert winners[0].claim_owner in (SESSION_A, SESSION_B)
    assert keeper.execute("SELECT COUNT(*) FROM outbox WHERE status='claimed'").fetchone()[0] == 1

    print("R23_RACE " + json.dumps({"winners": len(winners), "owner": winners[0].claim_owner}))


def test_public_callers_and_guard_logic_unchanged():
    """Source invariants: default path kept, guards intact, no public caller passes owner."""
    src = Path(oc.__file__).read_text(encoding="utf-8")
    claim_src = src[src.index("    def _claim_next(") : src.index("    def _release(")]

    assert "_QUARANTINE_CLAIM_GUARD = \"(claim_owner IS NULL OR claim_owner NOT LIKE 'quarantine:%')\"" in src
    assert claim_src.count("_QUARANTINE_CLAIM_GUARD") >= 2
    assert "BEGIN IMMEDIATE" in claim_src
    assert "claimed_at < ?" in claim_src
    assert "rowcount" in claim_src
    assert "token = uuid.uuid4().hex" in claim_src
    assert "if owner is not None:" in claim_src
    assert "entry = self._claim_next(exclude=seen)" in src

    tree = ast.parse(src)
    client = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OutboxClient")
    method = next(node for node in client.body if isinstance(node, ast.FunctionDef) and node.name == "_claim_next")
    assert {a.arg for a in method.args.kwonlyargs} >= {"exclude", "lease_s", "owner"}
    # No call site anywhere in the module supplies the new optional owner.
    owner_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_claim_next"
        and any(kw.arg == "owner" for kw in node.keywords)
    ]
    assert owner_calls == [], "a public caller was changed to pass owner"
