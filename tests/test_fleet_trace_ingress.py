"""Negative-first acceptance for private, Serve-authenticated trace metadata."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistx import fleet_trace_ingress as gateway

TRACE = "0123456789abcdef0123456789abcdef"
SPAN = "0123456789abcdef"
TRACEPARENT = f"00-{TRACE}-{SPAN}-01"
LOGIN = "operator@example.com"


def envelope(event_id="one", node="x1-370", producer="assistx", trace_id=TRACE):
    return {
        "schema": "assistx.fleet.trace.v1",
        "event_id": event_id,
        "trace_id": trace_id,
        "span_id": SPAN,
        "session_id": "session-one",
        "node_id": node,
        "producer": producer,
        "operation": "tool",
        "status": "ok",
        "occurred_at": "2026-10-10T17:00:00Z",
        "sequence": 0,
    }


@pytest.fixture
def setup(monkeypatch, tmp_path):
    policy = {
        f"login:{LOGIN}": {"nodes": ["x1-370"], "producers": ["assistx"], "write": True, "read": True},
        "login:observer@example.com": {"nodes": ["xwing"], "producers": ["opencode"], "read": True},
        "tag:ingress": {"nodes": ["xwing"], "producers": ["opencode"], "write": True, "read": False},
    }
    monkeypatch.setenv("FLEET_TRACE_TRUSTED_SERVE", "1")
    monkeypatch.setenv("FLEET_TRACE_DB_PATH", str(tmp_path / "trace.db"))
    monkeypatch.setenv("FLEET_TRACE_AUTH_POLICY_JSON", json.dumps(policy))
    return TestClient(gateway.app), tmp_path / "trace.db"


def headers(login=LOGIN, traceparent=TRACEPARENT):
    result = {"Tailscale-User-Login": login, "Content-Type": "application/json"}
    if traceparent is not None:
        result["traceparent"] = traceparent
    return result


def test_no_proxy_trust_is_fail_closed(monkeypatch, setup):
    client, db = setup
    monkeypatch.delenv("FLEET_TRACE_TRUSTED_SERVE")
    assert client.post("/v1/events", headers=headers(), json=envelope()).status_code == 503
    assert not db.exists()


def test_default_denies_missing_policy(monkeypatch, setup):
    client, db = setup
    monkeypatch.delenv("FLEET_TRACE_AUTH_POLICY_JSON")
    assert client.post("/v1/events", headers=headers(), json=envelope()).status_code == 503
    assert not db.exists()


def test_spoofed_custom_user_header_does_not_authenticate(setup):
    client, db = setup
    h = headers()
    del h["Tailscale-User-Login"]
    h["X-User"] = LOGIN
    assert client.post("/v1/events", headers=h, json=envelope()).status_code == 403
    assert not db.exists()


def test_an_authenticated_user_requires_registered_role(setup):
    client, db = setup
    assert client.post("/v1/events", headers=headers("unknown@example.com"), json=envelope()).status_code == 403
    assert not db.exists()


def test_verified_ingest_replay_conflict_and_private_sqlite_file(setup):
    client, db = setup
    one = client.post("/v1/events", headers=headers(), json=envelope())
    assert one.status_code == 201
    result = one.json()
    assert result["accepted"] is True
    assert result["source_attested"] is False
    assert len(result["sha256"]) == 64
    assert one.headers["traceparent"] == TRACEPARENT
    assert db.stat().st_mode & 0o777 == 0o600
    replay = client.post("/v1/events", headers=headers(), json=envelope())
    assert replay.status_code == 200 and replay.json()["accepted"] is False
    changed = dict(envelope(), status="failed")
    assert client.post("/v1/events", headers=headers(), json=changed).status_code == 409
    with sqlite3.connect(db) as store:
        assert store.execute("select count(*) from trace_events").fetchone()[0] == 1


@pytest.mark.parametrize("bad,expected", [
    (None, 400),
    ("invalid", 400),
    ("00-" + "0" * 32 + "-" + SPAN + "-01", 400),
    ("00-" + TRACE + "-" + "0" * 16 + "-01", 400),
    ("00-" + TRACE[:-1] + "Z-" + SPAN + "-01", 400),
])
def test_request_context_rejects_invalid_header(setup, bad, expected):
    client, _ = setup
    assert client.post("/v1/events", headers=headers(traceparent=bad),
                       json=envelope()).status_code == expected


def test_request_trace_id_must_match_event_and_span_must_be_w3c(setup):
    client, _ = setup
    wrong = envelope(trace_id="a" * 32)
    assert client.post("/v1/events", headers=headers(), json=wrong).status_code == 422
    bad_span = dict(envelope(), span_id="0" * 16)
    assert client.post("/v1/events", headers=headers(), json=bad_span).status_code == 422


def test_raw_prompt_and_extra_secret_fields_rejected(setup):
    client, db = setup
    sensitive = dict(envelope(), raw_prompt="secret private content")
    assert client.post("/v1/events", headers=headers(), json=sensitive).status_code == 422
    assert not db.exists()


def test_authenticated_actor_cannot_claim_another_node_or_producer(setup):
    client, db = setup
    assert client.post("/v1/events", headers=headers(), json=envelope(node="xwing")).status_code == 403
    assert client.post("/v1/events", headers=headers(), json=envelope(producer="opencode")).status_code == 403
    assert not db.exists()


def test_tagged_node_requires_explicit_capability_and_policy(setup):
    client, db = setup
    body = envelope(node="xwing", producer="opencode")
    h = headers(login="")
    assert client.post("/v1/events", headers=h, json=body).status_code == 403
    h["Tailscale-App-Capabilities"] = json.dumps({
        gateway.WRITER_CAPABILITY: [{"principal": "tag:ingress"}]
    })
    assert client.post("/v1/events", headers=h, json=body).status_code == 201
    h["Tailscale-App-Capabilities"] = json.dumps({
        gateway.WRITER_CAPABILITY: [{"principal": "tag:unregistered"}]
    })
    assert client.post("/v1/events", headers=h, json=envelope(event_id="two", node="xwing", producer="opencode")).status_code == 403


def test_reader_is_explicit_and_node_filtered(setup):
    client, _ = setup
    assert client.post("/v1/events", headers=headers(), json=envelope("e1")).status_code == 201
    tag_header = headers(login="")
    tag_header["Tailscale-App-Capabilities"] = json.dumps({
        gateway.WRITER_CAPABILITY: [{"principal": "tag:ingress"}]
    })
    assert client.post("/v1/events", headers=tag_header,
                       json=envelope("e2", node="xwing", producer="opencode")).status_code == 201
    operator = client.get(f"/v1/traces/{TRACE}/events", headers=headers())
    assert operator.status_code == 200
    assert [x["envelope"]["event_id"] for x in operator.json()["events"]] == ["e1"]
    other = client.get(f"/v1/traces/{TRACE}/events",
                       headers=headers(login="observer@example.com"))
    assert other.status_code == 200
    assert [x["envelope"]["event_id"] for x in other.json()["events"]] == ["e2"]
    assert client.get(f"/v1/traces/{TRACE}/events",
                      headers=headers(login="unknown@example.com")).status_code == 403
    assert client.get(f"/v1/traces/{TRACE}/events",
                      headers=tag_header).status_code == 403


def test_query_bounds_and_full_database_enforced_without_eviction(monkeypatch, setup):
    client, db = setup
    monkeypatch.setattr(gateway, "MAX_DB_ROWS", 1)
    assert client.post("/v1/events", headers=headers(), json=envelope("e1")).status_code == 201
    assert client.post("/v1/events", headers=headers(), json=envelope("e2")).status_code == 507
    assert client.get(f"/v1/traces/{TRACE}/events?limit=1000", headers=headers()).status_code == 422
    with sqlite3.connect(db) as store:
        assert store.execute("select count(*) from trace_events").fetchone()[0] == 1


def test_payload_budget_rejects_oversized_request(setup):
    client, _ = setup
    oversized = b"{" + b"x" * 8200 + b"}"
    assert client.post("/v1/events", headers=headers(),
                       content=oversized).status_code == 413


def test_unsafe_or_world_readable_db_directory_refused(monkeypatch, setup):
    client, db = setup
    parent = db.parent
    original = parent.stat().st_mode & 0o777
    try:
        parent.chmod(0o755)
        assert client.post("/v1/events", headers=headers(), json=envelope()).status_code == 503
        assert not db.exists()
    finally:
        parent.chmod(original)


def test_missing_or_relative_database_path_cannot_silently_write(monkeypatch, setup):
    client, _ = setup
    monkeypatch.setenv("FLEET_TRACE_DB_PATH", "traces.db")
    assert client.post("/v1/events", headers=headers(), json=envelope()).status_code == 503


def test_bad_capability_header_does_not_bypass_user_scope(setup):
    client, db = setup
    h = headers(login="unapproved@example.com")
    h["Tailscale-App-Capabilities"] = json.dumps({
        gateway.WRITER_CAPABILITY: [{"principal": "tag:unregistered"}],
    })
    assert client.post("/v1/events", headers=h, json=envelope()).status_code == 403
    assert not db.exists()


def test_trace_data_stays_metadata_only_when_queried(setup):
    client, db = setup
    client.post("/v1/events", headers=headers(), json=envelope())
    query = client.get(f"/v1/traces/{TRACE}/events", headers=headers())
    assert query.status_code == 200
    assert query.headers["cache-control"] == "no-store"
    text = query.text.lower()
    for forbidden in ("raw_prompt", "password", "authorization", "bearer ", "api_key", "coordinates"):
        assert forbidden not in text
    assert query.json()["source_attested"] is False


def test_packaged_validator_matches_research_schema_and_allowlist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "src/assistx/fleet_trace_contract.py").read_bytes() == (
        root / "scripts/trace_metadata_contract.py"
    ).read_bytes()


def test_trace_auth_capability_names_are_fully_qualified():
    assert gateway.WRITER_CAPABILITY.startswith("nez.ninja/cap/")
    assert gateway.READER_CAPABILITY.startswith("nez.ninja/cap/")


def test_multi_node_same_trace_parent_links_without_leaking_to_narrow_reader(monkeypatch, setup):
    from assistx.fleet_trace_contract import inspect_lineage
    client, db = setup
    root = envelope("root")
    root["span_id"] = SPAN
    assert client.post("/v1/events", headers=headers(), json=root).status_code == 201
    tagged = headers(login="")
    tagged["Tailscale-App-Capabilities"] = json.dumps({
        gateway.WRITER_CAPABILITY: [{"principal": "tag:ingress"}]
    })
    child = envelope("child", node="xwing", producer="opencode")
    child["span_id"] = "fedcba9876543210"
    child["parent_span_id"] = SPAN
    assert client.post("/v1/events", headers=tagged, json=child).status_code == 201
    # A registered operator with both node scopes sees two distinct systems.
    policy = json.loads(os.environ["FLEET_TRACE_AUTH_POLICY_JSON"])
    policy[f"login:{LOGIN}"]["nodes"] = ["x1-370", "xwing"]
    monkeypatch.setenv("FLEET_TRACE_AUTH_POLICY_JSON", json.dumps(policy))
    response = client.get(f"/v1/traces/{TRACE}/events", headers=headers())
    assert response.status_code == 200
    rows = [x["envelope"] for x in response.json()["events"]]
    assert inspect_lineage(rows)["missing_parent_spans"] == 0
    assert inspect_lineage(rows)["distinct_nodes"] == 2
    assert inspect_lineage(rows)["source_authentication_verified"] is False
    # Scoped readers cannot see x1-370; incomplete parents must not be hidden.
    limited = client.get(f"/v1/traces/{TRACE}/events",
                         headers=headers(login="observer@example.com"))
    assert [x["envelope"]["event_id"] for x in limited.json()["events"]] == ["child"]
    assert inspect_lineage([x["envelope"] for x in limited.json()["events"]])["missing_parent_spans"] == 1


def test_local_health_does_not_claim_admitted_tracing(setup):
    client, _ = setup
    assert client.get("/healthz").json() == {"ready_for_auth_check": True}
