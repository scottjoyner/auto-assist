"""Real claim proof consumer tests: local mocked network, no live execution."""

from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_claim_lease import issue_current_status, issue_lease_proof
from assistx.trace_claim_live_executor import execute_authorized_probe, preflight
from assistx.trace_execution_adapter import TraceDenied, TraceReceiptStore


@pytest.fixture
def scenario(tmp_path):
    signer = Ed25519PrivateKey.generate()
    public = tmp_path / "pinned.pem"
    public.write_bytes(
        signer.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    public.chmod(0o600)
    audit = tmp_path / "audit"
    audit.mkdir(mode=0o700)
    task = {
        "id": "task-101",
        "ticket_type": "trace_probe",
        "kind": "trace_probe",
        "status": "CLAIMED",
        "target_agent_id": "xwing",
        "claimed_by": "xwing",
        "claim_id": "claim-9",
        "execution_attempt": 1,
        "lease_expires_at_ts": int(time.time() * 1000) + 60000,
        "required_capabilities": ["trace-probe"],
        "payload_json": '{"command_id":"probe.noop.v1"}',
    }
    env = {
        "FLEET_TRACE_PROBE_ENABLED": "true",
        "FLEET_TRACE_REAL_EXECUTION_ENABLED": "true",
        "FLEET_NODE_AUTH_TOKEN": "fixture-node-token",
        "FLEET_TRACE_ISSUER_ORIGIN": "https://assistx.example",
        "FLEET_TRACE_LEASE_VERIFIER_KEY_FILE": str(public),
    }
    return {"signer": signer, "audit": audit, "task": task, "env": env}


def transport(s, mode="ok"):
    calls = []

    def http(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if mode == "unreachable":
            return 0, {}
        assert method == "POST" and kwargs["headers"]["X-Fleet-Node-Token"] == "fixture-node-token"
        if url.endswith("/claim-lease-proof"):
            if mode == "lease_rejected":
                return 409, {}
            lease = issue_lease_proof(
                s["task"],
                task_id=s["task"]["id"],
                claim_id="claim-9",
                node_id="xwing",
                execution_attempt=1,
                signer=s["signer"] if mode != "wrong_signature" else Ed25519PrivateKey.generate(),
            )
            return 200, {"lease_proof": lease}
        if url.endswith("/claim-current-status"):
            if mode in ("status_rejected", "status_unreachable"):
                return (409 if mode == "status_rejected" else 0), {}
            lease = kwargs["data"]["lease_proof"]
            if mode == "wrong_signature":
                return 409, {}
            status = issue_current_status(
                s["task"],
                lease_proof=lease,
                challenge=kwargs["data"]["challenge"],
                signer=s["signer"],
            )
            if mode == "stale_challenge":
                status["challenge"] = "f" * 64 if status["challenge"] != "f" * 64 else "e" * 64
            return 200, {"current_status": status}
        raise AssertionError("unexpected URL")

    return http, calls


def perform(s, http):
    return execute_authorized_probe(
        task=s["task"],
        node_id="xwing",
        claim_id="claim-9",
        audit_root=str(s["audit"]),
        assistx_url="https://assistx.example",
        auth=("fixture-user", "fixture-password"),
        env=s["env"],
        http=http,
    )


def test_happy_path_fsync_proof_binding_and_replay(scenario):
    http, calls = transport(scenario)
    result = perform(scenario, http)
    assert result["assistx_claim_verified"] is True
    assert result["status"] == "DONE" and len(calls) == 2
    assert all(c[2]["timeout"] == 5 for c in calls)
    store = TraceReceiptStore(str(scenario["audit"]), node_id="xwing")
    assert store.verify()["records"] == 2
    rows = [json.loads(x) for x in (scenario["audit"] / "journal.jsonl").read_text().splitlines()]
    assert rows[0]["signed_grant_sha256"] == rows[1]["signed_grant_sha256"]
    with pytest.raises(TraceDenied, match="attempt_already_recorded"):
        perform(scenario, http)


@pytest.mark.parametrize(
    "mode,code",
    [
        ("unreachable", "real_claim_lease_issuer_unavailable"),
        ("lease_rejected", "real_claim_lease_issuer_unavailable"),
        ("status_rejected", "real_claim_current_state_unavailable"),
        ("status_unreachable", "real_claim_current_state_unavailable"),
        ("wrong_signature", "real_claim_current_state_unavailable"),
        ("stale_challenge", "current_status_challenge_mismatch"),
    ],
)
def test_issuer_denial_never_creates_audit(scenario, mode, code):
    http, _ = transport(scenario, mode)
    with pytest.raises(TraceDenied, match=code):
        perform(scenario, http)
    assert not (scenario["audit"] / "journal.jsonl").exists()


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("FLEET_TRACE_REAL_EXECUTION_ENABLED", "false", "real_trace_execution_disabled"),
        ("FLEET_TRACE_PROBE_ENABLED", "false", "real_trace_execution_disabled"),
        ("FLEET_NODE_AUTH_TOKEN", "", "missing_fleet_node_identity_token"),
        ("FLEET_TRACE_LEASE_VERIFIER_KEY_FILE", "", "missing_real_issuer_verification_key"),
    ],
)
def test_disabled_or_missing_prerequisite_denies_before_http(scenario, field, value, error):
    scenario["env"][field] = value

    def forbidden(*args, **kwargs):
        raise AssertionError("network attempted before local admission")

    with pytest.raises(TraceDenied, match=error):
        perform(scenario, forbidden)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "READY"},
        {"kind": "llm"},
        {"ticket_type": "task"},
        {"claimed_by": "other-node"},
        {"claim_id": "old-claim"},
        {"execution_attempt": None},
        {"payload_json": '{"command_id":"shell.command"}'},
    ],
)
def test_claim_mutation_cannot_contact_issuer(scenario, change):
    scenario["task"].update(change)
    http, calls = transport(scenario)
    with pytest.raises(TraceDenied):
        perform(scenario, http)
    assert not calls
    assert not (scenario["audit"] / "journal.jsonl").exists()


def test_nonapproved_node_and_plaintext_remote_rejected(scenario):
    with pytest.raises(TraceDenied, match="unapproved_real_execution_node"):
        preflight(node_id="other-node", audit_root=str(scenario["audit"]), env=scenario["env"])
    http, calls = transport(scenario)
    with pytest.raises(TraceDenied, match="lease_issuer_plaintext_remote_denied"):
        execute_authorized_probe(
            task=scenario["task"],
            node_id="xwing",
            claim_id="claim-9",
            audit_root=str(scenario["audit"]),
            assistx_url="http://remote.example",
            auth=None,
            env=scenario["env"],
            http=http,
        )
    assert not calls


def test_worker_lifecycle_claim_two_proofs_and_completion(scenario, monkeypatch):
    from assistx import fleet_node_agent

    monkeypatch.setenv("FLEET_NODE_ID", "xwing")
    monkeypatch.setenv("FLEET_TRACE_EXECUTION_AUDIT_ROOT", str(scenario["audit"]))
    for k, v in scenario["env"].items():
        monkeypatch.setenv(k, v)
    _, transport_calls = transport(scenario)
    calls = []
    proof_http, _ = transport(scenario)

    def http(method, url, *, data=None, headers=None, **kwargs):
        calls.append((url, data, headers))
        if url.endswith("/claim"):
            assert headers["X-Fleet-Node-Token"] == "fixture-node-token"
            assert data["lease_seconds"] == 60
            return 200, {"claimed": True, "task": scenario["task"]}
        if url.endswith("/complete"):
            assert headers["X-Fleet-Node-Token"] == "fixture-node-token"
            return 200, {"ok": True}
        if "/api/fleet/trace-execution/" in url:
            return proof_http(method, url, data=data, headers=headers, **kwargs)
        raise AssertionError("unexpected remote URL")

    monkeypatch.setattr(fleet_node_agent, "_http", http)
    fleet_node_agent._claim_and_run(
        assistx_url="https://assistx.example",
        router_url="unused",
        auth=("fixture-user", "fixture-password"),
        node_id="xwing",
        caps=["trace-probe"],
        task=scenario["task"],
        lmstudio_url=None,
    )
    assert len([row for row in calls if row[0].endswith("/claim")]) == 1
    end = [row for row in calls if row[0].endswith("/complete")]
    assert len(end) == 1
    assert end[0][1]["status"] == "DONE"
    assert end[0][1]["result"]["assistx_claim_verified"] is True
    assert TraceReceiptStore(str(scenario["audit"]), node_id="xwing").verify()["records"] == 2


def test_worker_does_not_claim_trace_if_local_preflight_fails(scenario, monkeypatch):
    from assistx import fleet_node_agent

    monkeypatch.setenv("FLEET_NODE_ID", "xwing")
    monkeypatch.setenv("FLEET_TRACE_EXECUTION_AUDIT_ROOT", str(scenario["audit"]))
    for k, v in scenario["env"].items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FLEET_TRACE_REAL_EXECUTION_ENABLED", "false")

    def no_http(*args, **kwargs):
        raise AssertionError("no remote claim permitted")

    monkeypatch.setattr(fleet_node_agent, "_http", no_http)
    assert (
        fleet_node_agent._claim_and_run(
            assistx_url="https://assistx.example",
            router_url="unused",
            auth=None,
            node_id="xwing",
            caps=["trace-probe"],
            task=scenario["task"],
            lmstudio_url=None,
        )
        is False
    )
    assert not (scenario["audit"] / "journal.jsonl").exists()


def test_advertised_capability_requires_all_real_prerequisites(scenario, monkeypatch):
    from assistx import fleet_node_agent

    monkeypatch.setenv("FLEET_NODE_ID", "xwing")
    monkeypatch.setenv("FLEET_TRACE_EXECUTION_AUDIT_ROOT", str(scenario["audit"]))
    monkeypatch.setenv("FLEET_CAPABILITIES", "trace-probe")
    for k, v in scenario["env"].items():
        monkeypatch.setenv(k, v)
    caps, _ = fleet_node_agent._detect_capabilities(None)
    assert "trace-probe" in caps
    monkeypatch.setenv("FLEET_TRACE_REAL_EXECUTION_ENABLED", "false")
    caps, _ = fleet_node_agent._detect_capabilities(None)
    assert "trace-probe" not in caps


def test_issuer_origin_required_for_preflight(scenario):
    scenario["env"].pop("FLEET_TRACE_ISSUER_ORIGIN")
    with pytest.raises(TraceDenied, match="missing_pinned_issuer_origin"):
        preflight(node_id="xwing", audit_root=str(scenario["audit"]), env=scenario["env"])


def test_unapproved_issuer_origin_denied_without_remote_calls(scenario):
    scenario["env"]["FLEET_TRACE_ISSUER_ORIGIN"] = "https://second.example"
    http, calls = transport(scenario)
    with pytest.raises(TraceDenied, match="issuer_origin_not_pinned"):
        perform(scenario, http)
    assert not calls


@pytest.mark.parametrize(
    "origin", ["https://assistx.example/path", "https://assistx.example?q=1", "https://assistx.example:wrong"]
)
def test_malformed_issuer_origin_denied(scenario, origin):
    scenario["env"]["FLEET_TRACE_ISSUER_ORIGIN"] = origin
    http, calls = transport(scenario)
    with pytest.raises(TraceDenied, match="unsafe_lease_issuer_url"):
        perform(scenario, http)
    assert calls == []


def test_worker_denies_mismatched_issuer_before_claim(scenario, monkeypatch):
    from assistx import fleet_node_agent

    monkeypatch.setenv("FLEET_NODE_ID", "xwing")
    monkeypatch.setenv("FLEET_TRACE_EXECUTION_AUDIT_ROOT", str(scenario["audit"]))
    for name, value in scenario["env"].items():
        monkeypatch.setenv(name, value)

    def no_request(*args, **kwargs):
        raise AssertionError("unexpected remote request")

    monkeypatch.setattr(fleet_node_agent, "_http", no_request)
    assert (
        fleet_node_agent._claim_and_run(
            assistx_url="https://second.example",
            router_url="unused",
            auth=None,
            node_id="xwing",
            caps=["trace-probe"],
            task=scenario["task"],
            lmstudio_url=None,
        )
        is False
    )


def test_corrupted_existing_audit_denies_admission(scenario):
    journal = scenario["audit"] / "journal.jsonl"
    journal.write_text('{"not":"a valid record"}\n')
    journal.chmod(0o600)
    with pytest.raises(TraceDenied, match="audit_chain_invalid"):
        preflight(node_id="xwing", audit_root=str(scenario["audit"]), env=scenario["env"])


def test_near_backup_snapshot_limit_denies_without_truncating(scenario):
    journal = scenario["audit"] / "journal.jsonl"
    with journal.open("wb") as file:
        file.truncate(7 * 1024 * 1024)
    journal.chmod(0o600)
    before = journal.stat().st_size
    with pytest.raises(TraceDenied, match="trace_journal_archive_capacity_near_limit"):
        preflight(node_id="xwing", audit_root=str(scenario["audit"]), env=scenario["env"])
    assert journal.stat().st_size == before


def test_low_disk_capacity_denies_without_audit_mutation(scenario, monkeypatch):
    from assistx import trace_claim_live_executor

    class LowVolume:
        f_bavail = 1
        f_frsize = 4096

    monkeypatch.setattr(trace_claim_live_executor.os, "statvfs", lambda path: LowVolume())
    with pytest.raises(TraceDenied, match="trace_disk_capacity_too_low"):
        preflight(node_id="xwing", audit_root=str(scenario["audit"]), env=scenario["env"])
    assert not (scenario["audit"] / "journal.jsonl").exists()
