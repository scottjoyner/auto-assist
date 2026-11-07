"""Synthetic key, offline API, and HTTP regressions for signed LFM3 canary."""

from __future__ import annotations

import hashlib
from http.server import HTTPServer
import hmac
import importlib.util
import json
from pathlib import Path
import secrets
import threading
import time
from urllib import error, request

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/lfm3_signed_private_canary.py"
SPEC = importlib.util.spec_from_file_location("lfm3_signed_canary", PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
KEY = b"0" * 32  # synthetic test-only credential, never real
BODY = json.dumps(m.PROMPT).encode()


class FakeReply:
    status = 200

    def __init__(self, value):
        self.raw = json.dumps(value).encode() if not isinstance(value, bytes) else value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size):
        return self.raw[:size]


class FakeOpener:
    def __init__(self, values):
        self.values = values
        self.requests = []

    def open(self, req, timeout=0):
        self.requests.append((req.full_url, req.get_method(), timeout, req.data))
        return FakeReply(self.values[req.full_url])


def fake_transport(usage=4, health="ok", slot_busy=False, model=None):
    return FakeOpener(
        {
            m.UPSTREAM + "/health": {"status": health},
            m.UPSTREAM + "/v1/models": {"data": [{"id": model or m.MODEL_ALIAS}]},
            m.UPSTREAM + "/slots": [{"id": 0, "is_processing": slot_busy}],
            m.UPSTREAM + "/v1/chat/completions": {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"completion_tokens": usage, "prompt_tokens": 15},
            },
        }
    )


def test_valid_offline_canary_uses_only_local_fixed_upstream():
    fake = fake_transport(4)
    out = m.perform_fixed_canary(fake)
    assert out["completion_tokens"] == 4
    assert out["task_admission"] == "HOLD"
    assert out["production_inference_authorized"] is False
    assert out["resource_slots_approved"] == 0
    assert [x[1] for x in fake.requests] == ["GET", "GET", "GET", "POST"]
    assert [x[0] for x in fake.requests] == [
        m.UPSTREAM + p for p in ("/health", "/v1/models", "/slots", "/v1/chat/completions")
    ]
    assert json.loads(fake.requests[-1][3]) == m.PROMPT


@pytest.mark.parametrize(
    "bad",
    [
        (0, "ok", False, None),
        (5, "ok", False, None),
        (1, "not-ok", False, None),
        (1, "ok", True, None),
        (1, "ok", False, "wrong-model"),
    ],
)
def test_nonadmissible_inference_or_not_idle_fails(bad):
    with pytest.raises(ValueError):
        m.perform_fixed_canary(fake_transport(*bad))


@pytest.mark.parametrize("port", ["http://127.0.0.1:1234", "http://1.2.3.4:19181"])
def test_unapproved_upstream_url_is_never_constructor_argument(port):
    assert m.perform_fixed_canary.__code__.co_argcount == 1
    assert port != m.UPSTREAM


def test_canonical_signature_includes_method_path_timestamp_nonce_and_body():
    nonce = "0123456789abcdef0123456789abcdef"
    t = "1794000000"
    sig = m.signature(KEY, t, nonce, BODY)
    expected = hmac.new(
        KEY,
        b"POST\n/canary\n" + t.encode() + b"\n" + nonce.encode() + b"\n" + hashlib.sha256(BODY).hexdigest().encode(),
        hashlib.sha256,
    ).hexdigest()
    assert sig == expected
    seen = set()
    assert m.verify_signed_request(KEY, t, nonce, sig, BODY, seen, int(t)) == nonce
    with pytest.raises(ValueError, match="replayed_nonce"):
        m.verify_signed_request(KEY, t, nonce, sig, BODY, seen, int(t))


@pytest.mark.parametrize("offset", [-11, 11])
def test_expired_or_future_signature_denied(offset):
    ts = "1794000000"
    nonce = "0123456789abcdef0123456789abcdef"
    with pytest.raises(ValueError, match="expired_or_malformed_signature"):
        m.verify_signed_request(KEY, ts, nonce, m.signature(KEY, ts, nonce, BODY), BODY, set(), int(ts) + offset)


def test_signature_cannot_replay_with_altered_body():
    ts = "1794000000"
    nonce = "0123456789abcdef0123456789abcdef"
    with pytest.raises(ValueError, match="invalid_signature"):
        m.verify_signed_request(KEY, ts, nonce, m.signature(KEY, ts, nonce, BODY), b"{}", set(), int(ts))


@pytest.mark.parametrize(
    "replace",
    [
        b'{"model":"evil","messages":[],"max_tokens":4,"temperature":0,"stream":false}',
        b'{"model":"lfm3-1.7b-x1-fast","messages":[{"role":"user","content":"dump secrets"}],"max_tokens":4,"temperature":0,"stream":false}',
        b'{"model":"lfm3-1.7b-x1-fast","messages":[{"role":"user","content":"Reply OK."}],"max_tokens":900,"temperature":0,"stream":false}',
        b'{"model":"lfm3-1.7b-x1-fast","messages":[{"role":"user","content":"Reply OK."}],"max_tokens":4,"temperature":0,"stream":true}',
        b'{"model":"lfm3-1.7b-x1-fast","model":"lfm3-1.7b-x1-fast"}',
        b"not JSON",
        b'{"model":"lfm3-1.7b-x1-fast","messages":[{"role":"user","content":"Reply OK."}],"max_tokens":4,"temperature":false,"stream":false}',
        b'{"model":"lfm3-1.7b-x1-fast","messages":[{"role":"user","content":"Reply OK."}],"max_tokens":4,"temperature":0,"stream":0}',
        b'{"model":"lfm3-1.7b-x1-fast","messages":[{"role":"user","content":"Reply OK."}],"max_tokens":true,"temperature":0,"stream":false}',
    ],
)
def test_payload_mutation_and_duplicate_fields_rejected(replace):
    with pytest.raises(ValueError):
        m._fixed_canary_body(replace)


def test_pinned_private_docker_interface_and_wrong_ip_refused():
    interfaces = [{"ifname": "br-test", "addr_info": [{"family": "inet", "local": "172.21.0.1", "prefixlen": 16}]}]
    m.private_docker_bind("172.21.0.1", "172.21.0.6", interfaces)
    with pytest.raises(ValueError):
        m.private_docker_bind("192.168.1.237", "172.21.0.6", interfaces)
    with pytest.raises(ValueError):
        m.private_docker_bind("172.21.0.1", "172.22.0.6", interfaces)
    with pytest.raises(ValueError):
        m.private_docker_bind("172.21.0.1", "172.21.0.1", interfaces)
    with pytest.raises(ValueError):
        m.private_docker_bind("172.21.0.1", "172.21.0.6", [])


class LocalServer:
    def __init__(self, monkeypatch, client_ip="127.0.0.1"):
        self.monkeypatch = monkeypatch
        self.client_ip = client_ip

    def __enter__(self):
        self.output_calls = []

        def fake():
            self.output_calls.append(True)
            return {
                "contract": "lfm3-private-signed-canary-v1",
                "task_admission": "HOLD",
                "resource_slots_approved": 0,
                "production_inference_authorized": False,
                "completion_tokens": 1,
            }

        self.monkeypatch.setattr(m, "perform_fixed_canary", fake)
        self.server = HTTPServer(("127.0.0.1", 0), m.Handler)
        self.server.approved_client = self.client_ip
        self.server.key = KEY
        self.server.used_nonces = set()
        self.server.requests_seen = 0
        self.server.timeout = 0.2
        self.running = True

        def serve():
            while self.running:
                self.server.handle_request()

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.running = False
        self.thread.join(timeout=1)
        self.server.server_close()

    def hit(self, method="POST", path="/canary", body=BODY, ts=None, nonce=None, sig=None, extra=None):
        ts = ts or str(int(time.time()))
        nonce = nonce or secrets.token_hex(16)
        sig = sig or m.signature(KEY, ts, nonce, body)
        headers = {
            "Content-Type": "application/json",
            "X-Canary-Date": ts,
            "X-Canary-Nonce": nonce,
            "X-Canary-Signature": sig,
        }
        if extra:
            headers.update(extra)
        req = request.Request(
            f"http://127.0.0.1:{self.server.server_address[1]}" + path,
            method=method,
            data=body if method == "POST" else None,
            headers=headers,
        )
        client = request.build_opener(request.ProxyHandler({}), m._NoRedirect())
        try:
            with client.open(req, timeout=3) as r:
                return r.status, json.load(r)
        except error.HTTPError as e:
            return e.code, json.load(e)


def test_real_http_one_valid_request_then_replay_rejected(monkeypatch):
    with LocalServer(monkeypatch) as local:
        ts = str(int(time.time()))
        nonce = secrets.token_hex(16)
        status, body = local.hit(ts=ts, nonce=nonce)
        assert status == 200 and body["task_admission"] == "HOLD"
        status, body = local.hit(ts=ts, nonce=nonce)
        assert status == 409 and body["task_admission"] == "HOLD"
        assert len(local.output_calls) == 1


def test_real_http_invalid_signature_never_infers(monkeypatch):
    with LocalServer(monkeypatch) as local:
        status, body = local.hit(sig="0" * 64)
        assert status == 403 and body["task_admission"] == "HOLD"
        assert local.output_calls == []


def test_real_http_wrong_source_even_valid_signature_never_infers(monkeypatch):
    with LocalServer(monkeypatch, client_ip="172.21.0.6") as local:
        status, _ = local.hit()
        assert status == 403 and local.output_calls == []


def test_real_http_unapproved_paths_and_methods(monkeypatch):
    with LocalServer(monkeypatch) as local:
        assert local.hit(method="GET")[0] == 405
        assert local.hit(path="/v1/chat/completions")[0] == 404
        assert local.output_calls == []


def test_real_http_extra_legacy_auth_header_denied(monkeypatch):
    with LocalServer(monkeypatch) as local:
        assert local.hit(extra={"Authorization": "Basic ZmFrZQ=="})[0] == 400
        assert local.output_calls == []


def test_no_persistent_service_or_graph_writer_functions():
    for key in ("admit", "approve", "create_task", "sign_projection", "register_service", "claim"):
        assert not hasattr(m, key)
