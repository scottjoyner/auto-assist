"""Synthetic fail-closed private Docker HMAC→fenced host slot→LFM3 stage tests."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib import error, request

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "_lfm3_private_slot_bridge_test", SCRIPTS / "fleet_lfm3_signed_slot_bridge_canary.py"
)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)

SECRET = b"S" * 32
from lfm3_signed_private_canary import PROMPT  # noqa: E402

BODY = json.dumps(PROMPT, separators=(",", ":")).encode()


class FakeLedger:
    def __init__(self, *, fail_on_acquire=False, revoke_after_effect=False):
        self.acquires = []
        self.validates = 0
        self.releases = []
        self.fail_on_acquire = fail_on_acquire
        self.revoke_after_effect = revoke_after_effect

    def acquire(self, task, claim, *, duration_ms):
        self.acquires.append((task, claim, duration_ms))
        if self.fail_on_acquire:
            raise bridge.SlotLeaseDenied("synthetic_no_slot")
        return object()

    def validate_effect(self, lease):
        self.validates += 1
        if self.revoke_after_effect and self.validates >= 2:
            raise bridge.SlotLeaseDenied("synthetic_revocation")

    def release(self, lease):
        self.releases.append(lease)


@contextmanager
def local_server(*, allowed="127.0.0.1", ledger=None):
    with bridge.PrivateHTTPServer(("127.0.0.1", 0), bridge.Handler) as srv:
        srv.secret = SECRET
        srv.approved_client = allowed
        srv.ledger = ledger or FakeLedger()
        srv.requests_seen = 0
        srv.seen = set()
        thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield srv
        finally:
            srv.shutdown()
            thread.join(timeout=2)


def post(srv, *, body=BODY, stamp=None, nonce=None, signature=None, route="/staging-canary", source_headers=None):
    stamp = str(int(time.time())) if stamp is None else stamp
    nonce = "a" * 32 if nonce is None else nonce
    signature = bridge.sign(SECRET, stamp, nonce, body) if signature is None else signature
    headers = {
        "Content-Type": "application/json",
        "X-Canary-Date": stamp,
        "X-Canary-Nonce": nonce,
        "X-Canary-Signature": signature,
    }
    if source_headers:
        headers.update(source_headers)
    req = request.Request(
        f"http://127.0.0.1:{srv.server_port}{route}",
        data=body,
        method="POST",
        headers=headers,
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=3) as result:
            return result.status, json.load(result)
    except error.HTTPError as result:
        return result.code, json.load(result)


def test_defaults_to_hold_without_any_network_action(monkeypatch, capsys):
    monkeypatch.setattr(bridge, "serve", lambda *_a, **_k: pytest.fail("no side effects on default"))
    assert bridge.main([]) == 2
    assert json.loads(capsys.readouterr().out)["task_admission"] == "HOLD"


def test_staging_missing_args_still_hold_without_binding(capsys, monkeypatch):
    monkeypatch.setattr(bridge, "serve", lambda *_a, **_k: pytest.fail("must refuse incomplete bind"))
    assert bridge.main(["--staging"]) == 2
    assert json.loads(capsys.readouterr().out)["task_admission"] == "HOLD"


def test_exact_route_domain_separation_and_hmac_timing():
    now = int(time.time())
    sig = bridge.sign(SECRET, str(now), "a" * 32, BODY)
    assert len(sig) == 64
    assert bridge.verify_signed_request(SECRET, str(now), "a" * 32, sig, BODY, set(), now) == "a" * 32
    with pytest.raises(ValueError, match="wrong_signature"):
        from lfm3_signed_private_canary import signature as older

        bridge.verify_signed_request(
            SECRET, str(now), "a" * 32, older(SECRET, str(now), "a" * 32, BODY), BODY, set(), now
        )
    with pytest.raises(ValueError, match="invalid_or_stale"):
        bridge.verify_signed_request(
            SECRET, str(now - 30), "a" * 32, bridge.sign(SECRET, str(now - 30), "a" * 32, BODY), BODY, set(), now
        )
    with pytest.raises(ValueError, match="wrong_signature"):
        bridge.verify_signed_request(SECRET, str(now), "a" * 32, sig, b"tampered", set(), now)
    seen = {"a" * 32}
    with pytest.raises(ValueError, match="nonce_replay"):
        bridge.verify_signed_request(SECRET, str(now), "a" * 32, sig, BODY, seen, now)


def test_real_local_http_positive_is_nonadmitting_and_lease_bound(monkeypatch):
    calls = []

    def fake_upstream():
        calls.append("fixed_four_token_model")
        return {"completion_tokens": 4, "model_alias": "lfm3-1.7b-x1-fast", "model_text": "NEVER_RETURN_THIS"}

    monkeypatch.setattr(bridge, "run_bounded_fixed_inference", fake_upstream)
    ledger = FakeLedger()
    with local_server(ledger=ledger) as srv:
        status, data = post(srv)
        assert status == 200
        assert data["status"] == "STAGING_OBSERVED"
        assert data["task_admission"] == "HOLD"
        assert data["approved_parallel_slots"] == 0
        assert data["completion_tokens"] == 4
        assert data["host_slot_fenced_at_accept_and_result"] is True
        assert "model_text" not in data and "NEVER_RETURN_THIS" not in str(data)
        assert ledger.acquires[0][0] == "staging-lfm3-fixed-prompt"
        assert ledger.acquires[0][2] == bridge.MAX_SLOT_MS
        assert ledger.validates == 2
        assert len(ledger.releases) == 1 and len(calls) == 1
        replay, denied = post(srv)
        assert replay == 403 and denied["task_admission"] == "HOLD"
        assert len(calls) == 1


def test_denied_signed_request_never_mints_slot_or_infers(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "run_bounded_fixed_inference", lambda: calls.append(1))
    ledger = FakeLedger()
    with local_server(ledger=ledger) as srv:
        results = [
            post(srv, signature="0" * 64),
            post(srv, body=b'{"model":"wrong"}', nonce="b" * 32),
            post(srv, route="/not-staging", nonce="c" * 32),
        ]
        assert [r[0] for r in results] == [403, 403, 404]
        assert not ledger.acquires and not calls


def test_unapproved_source_ip_denied_even_with_valid_signature(monkeypatch):
    monkeypatch.setattr(bridge, "run_bounded_fixed_inference", lambda: pytest.fail("wrong source must not infer"))
    ledger = FakeLedger()
    with local_server(allowed="127.0.0.2", ledger=ledger) as srv:
        status, data = post(srv)
        assert status == 403 and data["reason"] == "source_not_approved"
        assert not ledger.acquires


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
def test_no_other_methods(method):
    with local_server() as srv:
        opener = request.build_opener(request.ProxyHandler({}))
        req = request.Request(f"http://127.0.0.1:{srv.server_port}/staging-canary", method=method)
        with pytest.raises(error.HTTPError) as exc:
            opener.open(req, timeout=2)
        assert exc.value.code == 405
        assert srv.ledger.acquires == []


def test_slot_exhaustion_no_model_calls(monkeypatch):
    monkeypatch.setattr(bridge, "run_bounded_fixed_inference", lambda: pytest.fail("no slot must not infer"))
    ledger = FakeLedger(fail_on_acquire=True)
    with local_server(ledger=ledger) as srv:
        code, result = post(srv)
        assert code == 423 and result["task_admission"] == "HOLD"
        assert ledger.validates == 0 and not ledger.releases


def test_post_model_revocation_withholds_response_and_releases(monkeypatch):
    observed = []
    monkeypatch.setattr(bridge, "run_bounded_fixed_inference", lambda: observed.append(1) or {"completion_tokens": 2})
    ledger = FakeLedger(revoke_after_effect=True)
    with local_server(ledger=ledger) as srv:
        code, result = post(srv)
        assert code == 423 and result["task_admission"] == "HOLD"
        assert len(observed) == 1 and ledger.validates == 2 and len(ledger.releases) == 1


def test_no_model_inference_when_malformed_or_missing_http_signature(monkeypatch):
    monkeypatch.setattr(bridge, "run_bounded_fixed_inference", lambda: pytest.fail("malformed must not infer"))
    with local_server() as srv:
        for value in ("0" * 64, "fake", "a" * 86):
            code, result = post(srv, signature=value, nonce=os.urandom(16).hex())
            assert code == 403 and result["task_admission"] == "HOLD"
        assert srv.ledger.acquires == []


def test_wrong_model_output_never_authorizes_gateway(monkeypatch):
    monkeypatch.setattr(bridge, "run_bounded_fixed_inference", lambda: {"completion_tokens": True})
    with local_server() as srv:
        code, result = post(srv)
        assert code == 423 and result["task_admission"] == "HOLD"


def test_server_rejects_non_docker_interface_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bridge.subprocess,
        "check_output",
        lambda *_a, **_k: json.dumps(
            [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "172.21.0.1", "prefixlen": 16}]}]
        ).encode(),
    )
    fd, writefd = os.pipe()
    try:
        os.write(writefd, SECRET)
        with pytest.raises(ValueError, match="docker_private_bind_not_attested"):
            bridge.serve("172.21.0.1", "172.21.0.6", 19203, fd, tmp_path / "missing.sqlite", max_seconds=1)
    finally:
        os.close(fd)
        os.close(writefd)


def test_no_self_grant_when_missing_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bridge.subprocess,
        "check_output",
        lambda *_a, **_k: json.dumps(
            [{"ifname": "br-docker-test", "addr_info": [{"family": "inet", "local": "172.21.0.1", "prefixlen": 16}]}]
        ).encode(),
    )
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    fd, writefd = os.pipe()
    try:
        os.write(writefd, SECRET)
        with pytest.raises(bridge.SlotLeaseDenied):
            bridge.serve("172.21.0.1", "172.21.0.6", 19203, fd, private / "missing.sqlite", max_seconds=1)
    finally:
        os.close(writefd)
        # The serve method owns fd once the bind validation is complete.
        try:
            os.close(fd)
        except OSError:
            pass
    assert not (private / "missing.sqlite").exists()


def test_deadline_timeout_terminates_inference_child(monkeypatch):
    class PipeEnd:
        def __init__(self):
            self.closed = False

        def poll(self, _timeout):
            return False

        def close(self):
            self.closed = True

    class Child:
        def __init__(self):
            self.alive = False
            self.terminated = False
            self.exitcode = None

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False
            self.exitcode = -15

        def kill(self):
            pytest.fail("graceful terminate should suffice")

        def join(self, timeout=None):
            return None

    class Context:
        def __init__(self):
            self.receiver = PipeEnd()
            self.sender = PipeEnd()
            self.child = Child()

        def Pipe(self, *, duplex):
            assert duplex is False
            return (self.receiver, self.sender)

        def Process(self, *, target, args, daemon):
            assert target is bridge._bounded_model_child
            assert args == (self.sender,)
            assert daemon is True
            return self.child

    ctx = Context()
    monkeypatch.setattr(
        bridge.multiprocessing, "get_context", lambda mode: ctx if mode == "spawn" else pytest.fail("not spawn")
    )
    with pytest.raises(ValueError, match="bounded_fixed_inference_unavailable"):
        bridge.run_bounded_fixed_inference()
    assert ctx.child.terminated
    assert ctx.receiver.closed and ctx.sender.closed
    assert bridge.MAX_CHILD_SECONDS * 1000 < bridge.MAX_SLOT_MS


def test_duplicate_content_type_and_giant_content_length_denied_without_slots():
    import socket

    with local_server() as srv:
        for headers in (
            b"Content-Type: application/json\r\nContent-Type: application/json\r\nContent-Length: 2\r\n",
            b"Content-Type: application/json\r\nContent-Length: " + b"9" * 5000 + b"\r\n",
            b"Content-Type: application/json\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n",
        ):
            with socket.create_connection(("127.0.0.1", srv.server_port), timeout=2) as sock:
                sock.settimeout(2)
                req = b"POST /staging-canary HTTP/1.1\r\nHost: localhost\r\n" + headers + b"\r\n{}"
                sock.sendall(req)
                response = sock.recv(512)
                assert b" 400 " in response.split(b"\r\n")[0], response[:100]
        assert srv.ledger.acquires == []


def test_root_and_unbounded_serve_rejected_before_any_network(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge.os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="bounded_nonroot_staging_required"):
        bridge.serve("172.21.0.1", "172.21.0.6", 19203, 3, tmp_path / "nonexistent.sqlite")
    monkeypatch.setattr(bridge.os, "geteuid", lambda: 1000)
    with pytest.raises(ValueError, match="bounded_nonroot_staging_required"):
        bridge.serve("172.21.0.1", "172.21.0.6", 19203, 3, tmp_path / "nonexistent.sqlite", max_seconds=100)


def test_default_operator_probe_no_container_traffic(tmp_path):
    import subprocess
    import sys

    path = SCRIPTS / "fleet_lfm3_slot_bridge_operator_probe.py"
    result = subprocess.run([sys.executable, str(path)], cwd=tmp_path, text=True, capture_output=True, timeout=5)
    assert result.returncode == 2
    data = json.loads(result.stdout)
    assert data["status"] == "HOLD" and data["task_admission"] == "HOLD"
    assert list(tmp_path.iterdir()) == []


def test_revocation_between_effect_validation_and_release_never_returns_success(monkeypatch):
    monkeypatch.setattr(bridge, "run_bounded_fixed_inference", lambda: {"completion_tokens": 4})

    class RevokedAtRelease(FakeLedger):
        def release(self, lease):
            self.releases.append(lease)
            raise bridge.SlotLeaseDenied("revoked_before_release")

    ledger = RevokedAtRelease()
    with local_server(ledger=ledger) as srv:
        code, result = post(srv)
        assert code == 423
        assert result["task_admission"] == "HOLD"
        assert "completion_tokens" not in result
        assert ledger.validates == 2 and len(ledger.releases) == 1
