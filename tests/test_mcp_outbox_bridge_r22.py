"""Offline contract check for ``docs/mcp-outbox-bridge-r22.md``.

One test module, no production change, no live DB, no MCP server, no credentials:

1. Source assertions: the doc's outbox and MCP claims match the real code paths.
2. Tool-surface assertion: the real read-only MCP tool set offers no write tool.
3. In-memory fixture: a real ``OutboxClient`` claims a stale session-id-shaped
   claim and a session-id-labelled pending row with fresh tokens, leaves an
   operator quarantine hold untouched, and fences a release that presents the
   session id instead of the claim token.

The fixture is in-process and is an invariant check, not a capacity measurement,
a deployed bridge, or evidence about a live queue.
"""

import ast
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path

import pytest

import assistx.outbox_client as oc

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "mcp-outbox-bridge-r22.md"
SG_COORD_SRC = Path(
    os.environ.get(
        "R22_SG_COORD_SRC",
        "/home/scott/.sensorgraft-private/sensorgraph-coord-mcp/server.py",
    )
)

# Tool surface declared by the specification; provenance is the real MCP source
# cross-checked below when that reviewed file is present on this host.
EXPECTED_MCP_TOOLS = frozenset(
    {
        "sg_status",
        "sg_watermark",
        "sg_read_slice",
        "sg_query",
        "sg_neighbors",
        "sg_agent_list",
        "sg_observations",
    }
)
WRITE_TOOLS = frozenset(
    {
        "sg_write_db",
        "sg_write",
        "sg_insert",
        "sg_update",
        "sg_delete",
        "shell_exec",
        "sql_query",
        "tools/write_file",
        "commit",
        "checkpoint",
    }
)

SESSION_ID = "sess-" + "a" * 32
STALE_CLAIMED_AT = "2026-11-20T00:00:00+00:00"
QUARANTINE = "quarantine:r22-hold"
PAYLOAD = '{"note":"PAYLOAD_SENTINEL_NEVER_PRINTED"}'


def _doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _outbox_source() -> str:
    return Path(oc.__file__).read_text(encoding="utf-8")


def _claim_next_args(src: str) -> set[str]:
    tree = ast.parse(src)
    client = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OutboxClient")
    method = next(node for node in client.body if isinstance(node, ast.FunctionDef) and node.name == "_claim_next")
    args = method.args
    return {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]} | {
        a.arg for a in ([args.vararg] if args.vararg else [])
    }


def _mcp_tool_names(src: str) -> set[str]:
    """Names actually registered by available_tools() in the MCP server source."""
    tree = ast.parse(src)
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "available_tools")
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "extend":
            for arg in node.args:
                if isinstance(arg, ast.Tuple):
                    names.update(e.id for e in arg.elts if isinstance(e, ast.Name))
    return names


def test_doc_invariants_match_real_sources():
    """The r22 doc's code-grounded statements must match both real code paths."""
    doc = _doc()
    src = _outbox_source()

    # Quarantine is a claim_owner LIKE hold, applied to SELECT and guarded UPDATE.
    assert "_QUARANTINE_CLAIM_GUARD = \"(claim_owner IS NULL OR claim_owner NOT LIKE 'quarantine:%')\"" in src
    claim_src = src[src.index("    def _claim_next(") : src.index("    def _release(")]
    assert claim_src.count("_QUARANTINE_CLAIM_GUARD") >= 2
    assert "BEGIN IMMEDIATE" in claim_src
    assert "token = uuid.uuid4().hex" in claim_src
    assert "rowcount" in claim_src
    assert "claimed_at < ?" in claim_src
    # The doc says there is no caller-supplied owner: the AST confirms it.
    assert not _claim_next_args(src) & {"owner", "claim_owner", "session_id"}
    release_src = src[src.index("    def _release(") : src.index("    def _backoff_remaining_s(")]
    assert "AND status='claimed' AND claim_owner=?" in release_src
    assert 'ASSISTX_OUTBOX_CLAIM_LEASE_S", "90"' in src
    assert "urlopen(req, timeout=15)" in src
    assert "heartbeat" not in src  # outbox has no heartbeat; MCP's is separate

    # Every envelope / boundary phrase in the doc is present in the sources or
    # in the named predecessor doc; none of the write tool names is offered.
    for phrase in (
        "SPECIFICATION ONLY",
        "separate SQLite files",
        "no write tool",
        "coordination_db_absent_or_unconfigured",
        "N<=32",
        "effective committers",
        "caller-supplied owner parameter",
        "Owner-gated",
    ):
        assert phrase in doc, phrase
    assert "PRAGMA query_only=ON" in doc
    assert "mode=ro" in doc
    assert not any(f"`{name}`" in doc for name in WRITE_TOOLS)


def test_mcp_read_only_tool_set_has_no_write_tool():
    """The doc's seven-tool surface matches the real MCP source, with no write tool."""
    doc = _doc()
    for name in EXPECTED_MCP_TOOLS:
        assert f"`{name}`" in doc, name
    for name in WRITE_TOOLS:
        assert f"`{name}`" not in doc, name

    if not SG_COORD_SRC.is_file():
        pytest.skip(f"reviewed MCP source not present: {SG_COORD_SRC}")
    src = SG_COORD_SRC.read_text(encoding="utf-8")
    assert _mcp_tool_names(src) == set(EXPECTED_MCP_TOOLS)
    defined = {node.name for node in ast.parse(src).body if isinstance(node, ast.FunctionDef)}
    assert not defined & WRITE_TOOLS
    assert not [n for n in defined if re.search(r"write|insert|delete|commit|checkpoint|shell", n, re.I)]
    assert '"?mode=ro&cache=private"' in src
    assert "PRAGMA query_only=ON" in src
    assert '"coordination_db_absent_or_unconfigured"' in src
    assert '"SG_COORD_DB"' in src
    assert '"SENSORGRAPH_COORD_SURFACE", "off"' in src


class InMemorySqlite:
    """sqlite3 facade: route production connect() calls to a shared memory DB."""

    Error = sqlite3.Error

    @staticmethod
    def connect(database, *args, **kwargs):
        kwargs["uri"] = True
        return sqlite3.connect(database, *args, **kwargs)


@pytest.fixture
def mem_outbox(monkeypatch):
    uri = f"file:coord-r22-{uuid.uuid4().hex}?mode=memory&cache=shared"
    monkeypatch.setattr(oc, "sqlite3", InMemorySqlite)
    keeper = sqlite3.connect(uri, uri=True)
    try:
        oc.OutboxClient(db_path=uri, api_url="", auto_flush=False)
        yield uri, keeper
    finally:
        keeper.close()


def test_inmemory_session_shaped_claim_owner_is_claimable(mem_outbox):
    """A session-shaped owner is claimable; quarantine is not; the token is the fence."""
    _, keeper = mem_outbox
    rows = [
        # Expired lease whose owner is session-id-shaped: recoverable with a new token.
        ("sess-stale", "2026-11-20T00:00:02+00:00", "claimed", SESSION_ID, STALE_CLAIMED_AT),
        # Pending row merely labelled with a session id: claimable, guard excludes only quarantine.
        ("sess-pending", "2026-11-21T00:00:00+00:00", "pending", SESSION_ID, None),
        # Operator hold, oldest and with an expired claimed_at: must stay put.
        ("held", "2026-11-19T00:00:00+00:00", "pending", QUARANTINE, STALE_CLAIMED_AT),
    ]
    keeper.executemany(
        "INSERT INTO outbox (outbox_id, event_id, payload_json, status, created_at, claim_owner, claimed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(oid, "e-" + oid, PAYLOAD, status, created, owner, claimed) for oid, created, status, owner, claimed in rows],
    )
    keeper.commit()

    client = oc.OutboxClient(db_path=mem_outbox[0], api_url="", auto_flush=False)
    first = client._claim_next()
    second = client._claim_next()
    third = client._claim_next()
    assert first is not None and second is not None
    assert third is None, "a third deliverable row was claimed from a 2-row fixture"
    assert {first.outbox_id, second.outbox_id} == {"sess-stale", "sess-pending"}
    for entry in (first, second):
        assert entry.claim_owner != SESSION_ID
        assert len(entry.claim_owner) == 32
        int(entry.claim_owner, 16)  # a fresh uuid4().hex token, not the session label
    assert first.claim_owner != second.claim_owner

    state = {row[0]: row for row in keeper.execute("SELECT outbox_id, status, claim_owner, claimed_at FROM outbox")}
    stale = state["sess-stale"]
    assert stale[1] == "claimed" and stale[2] != SESSION_ID
    assert stale[3] != STALE_CLAIMED_AT, "expired session-id claim was not renewed"
    assert state["held"] == ("held", "pending", QUARANTINE, STALE_CLAIMED_AT)

    # The returned token is the authority; presenting the session id is fenced out.
    assert client._release(first, "delivered", 1) is True
    forged = oc.OutboxEntry(
        outbox_id=second.outbox_id,
        event_id=second.event_id,
        payload_json=second.payload_json,
        status="claimed",
        claim_owner=SESSION_ID,
    )
    assert client._release(forged, "delivered", 1) is False
    assert state["sess-pending"][2] != SESSION_ID
    assert (
        keeper.execute("SELECT claim_owner FROM outbox WHERE outbox_id=?", (second.outbox_id,)).fetchone()[0]
        == second.claim_owner
    )

    print(
        "R22_INMEM "
        + json.dumps(
            {
                "claimed": [first.outbox_id, second.outbox_id],
                "quarantine_untouched": True,
                "session_id_release_fenced": True,
            }
        )
    )
