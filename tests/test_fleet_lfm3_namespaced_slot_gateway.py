"""Offline negative and fixed-shape tests for the LFM3 namespace gateway.

No Docker, model process, network namespace, signed owner grant or Task call.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "_lfm3_namespaced_slot_stage_contract",
    SCRIPTS / "fleet_lfm3_namespaced_slot_gateway.py",
)
assert SPEC and SPEC.loader
ns = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ns
SPEC.loader.exec_module(ns)


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, length):
        assert length == ns.MAX_BYTES + 1
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode()


class FakeTransport:
    def __init__(
        self, *, status="ok", model=ns.MODEL_ALIAS, busy=False, tokens=4, choices=None, slots=None, health=None
    ):
        self.calls = []
        self.status = status
        self.model = model
        self.busy = busy
        self.tokens = tokens
        self.choices = choices
        self.slots = slots
        self.health = health

    def open(self, req, timeout=0):
        self.calls.append((req.full_url, req.get_method(), req.data, timeout))
        assert req.full_url.startswith(ns.UPSTREAM)
        assert timeout == 5
        if req.full_url.endswith("/health"):
            return FakeResponse(self.health if self.health is not None else {"status": self.status})
        if req.full_url.endswith("/v1/models"):
            return FakeResponse({"data": [{"id": self.model}]})
        if req.full_url.endswith("/slots"):
            return FakeResponse(self.slots if self.slots is not None else [{"id": 0, "is_processing": self.busy}])
        if req.full_url.endswith("/v1/chat/completions"):
            return FakeResponse(
                {
                    "choices": self.choices if self.choices is not None else [{"message": {"content": "OK"}}],
                    "usage": {"completion_tokens": self.tokens},
                }
            )
        pytest.fail("nonallowlisted upstream request")


def test_exact_fixed_private_model_shape(monkeypatch):
    client = FakeTransport()
    monkeypatch.setattr(ns.request, "build_opener", lambda *_a: client)
    result = ns.fixed_namespaced_model()
    assert result == {"completion_tokens": 4}
    assert [x[1] for x in client.calls] == ["GET", "GET", "GET", "POST"]
    assert all(x[0].startswith("http://127.0.0.1:19185/") for x in client.calls)
    body = json.loads(client.calls[-1][2])
    assert body == {
        "model": ns.MODEL_ALIAS,
        "messages": [{"role": "user", "content": "Reply OK."}],
        "temperature": 0,
        "max_tokens": 4,
        "stream": False,
    }
    assert "tools" not in body


@pytest.mark.parametrize(
    "changes, stage",
    [
        ({"status": "loading"}, 3),
        ({"model": "other-model"}, 3),
        ({"busy": True}, 3),
        ({"slots": [{"id": 0, "is_processing": False}, {"id": 1, "is_processing": False}]}, 3),
        ({"slots": []}, 3),
        ({"tokens": 0}, 4),
        ({"tokens": 5}, 4),
        ({"tokens": True}, 4),
        ({"choices": []}, 4),
        ({"health": None}, 4),
    ],
)
def test_model_residency_busy_slot_or_token_forgery_denies(monkeypatch, changes, stage):
    client = FakeTransport(**changes)
    monkeypatch.setattr(ns.request, "build_opener", lambda *_a: client)
    if changes == {"health": None}:
        # Null was default in the transport fixture; valid result here.
        assert ns.fixed_namespaced_model() == {"completion_tokens": 4}
    else:
        with pytest.raises(ValueError):
            ns.fixed_namespaced_model()
    if changes.get("tokens") is not None or "choices" in changes or changes == {"health": None}:
        assert len(client.calls) == 4
    else:
        assert len(client.calls) == 3


def test_null_health_or_nonfinite_json_denied():
    class Transport(FakeTransport):
        def open(self, req, timeout=0):
            self.calls.append(req.full_url)
            if req.full_url.endswith("/health"):
                return FakeResponse(b"null")
            return super().open(req, timeout)

    with pytest.raises(ValueError):
        ns._fetch_fixed(Transport(), "GET", "/no-other-route")
    assert ns._fetch_fixed(Transport(), "GET", "/health") is None

    class Nonfinite(FakeTransport):
        def open(self, req, timeout=0):
            return FakeResponse(b'{"status": NaN}')

    with pytest.raises(ValueError, match="nonfinite_json"):
        ns._fetch_fixed(Nonfinite(), "GET", "/health")


def test_untrusted_model_urls_are_not_forwardable():
    with pytest.raises(ValueError, match="nonfixed_model_route"):
        ns._fetch_fixed(FakeTransport(), "POST", "/v1/models", b"{}")
    with pytest.raises(ValueError, match="nonfixed_model_route"):
        ns._fetch_fixed(FakeTransport(), "GET", "http://example.com/")


class FakeSocket:
    def __init__(self, *, bypass=False, health=True, deny_bind=False):
        self.bypass = bypass
        self.health = health
        self.deny_bind = deny_bind
        self.bound = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def settimeout(self, value):
        pass

    def bind(self, where):
        if self.deny_bind:
            raise OSError("not assigned in namespace")
        self.bound.append(where)

    def connect_ex(self, where):
        if where[1] == 19185 and where[0] == "127.0.0.1":
            return 0 if self.health else 111
        return 0 if self.bypass else 111


@pytest.mark.parametrize(
    "bind,caller",
    [
        ("127.0.0.1", "172.21.0.6"),
        ("8.8.8.8", "172.21.0.6"),
        ("172.21.0.7", "8.8.8.8"),
        ("172.21.0.7", "127.0.0.1"),
        ("172.21.0.7", "172.21.0.7"),
        ("::1", "172.21.0.6"),
    ],
)
def test_missing_private_namespace_identity_denies(bind, caller):
    with pytest.raises(ValueError, match="unverified_namespaced_inference_topology"):
        ns.verify_isolated_namespace(bind, caller, ns.HMAC_PORT)


@pytest.mark.parametrize(
    "condition",
    [
        {"bypass": True},
        {"health": False},
        {"deny_bind": True},
    ],
)
def test_no_model_bypass_or_unverified_bind_accepted(monkeypatch, condition):
    monkeypatch.setattr(ns.socket, "socket", lambda: FakeSocket(**condition))
    with pytest.raises(ValueError, match="unverified_namespaced_inference_topology"):
        ns.verify_isolated_namespace("172.21.0.7", "172.21.0.6", ns.HMAC_PORT)


def test_valid_verified_isolated_network_synthetic(monkeypatch):
    monkeypatch.setattr(ns.socket, "socket", lambda: FakeSocket())
    ns.verify_isolated_namespace("172.21.0.7", "172.21.0.6", ns.HMAC_PORT)


def test_default_cli_exits_hold_before_network(monkeypatch, capsys):
    monkeypatch.setattr(ns, "serve", lambda *_a, **_k: pytest.fail("default cannot start services"))
    assert ns.main([]) == 2
    assert json.loads(capsys.readouterr().out)["task_admission"] == "HOLD"


def test_staging_missing_args_exits_hold_before_network(monkeypatch, capsys):
    monkeypatch.setattr(ns, "serve", lambda *_a, **_k: pytest.fail("missing inputs cannot start"))
    assert ns.main(["--staging"]) == 2
    assert json.loads(capsys.readouterr().out)["task_admission"] == "HOLD"


def test_root_invalid_fd_bad_timeout_denied_before_listen(monkeypatch, tmp_path):
    monkeypatch.setattr(ns.os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="nonroot_bounded_stdin_staging_only"):
        ns.serve("172.21.0.7", "172.21.0.6", tmp_path / "grant.sqlite")
    monkeypatch.setattr(ns.os, "geteuid", lambda: 1000)
    with pytest.raises(ValueError, match="nonroot_bounded_stdin_staging_only"):
        ns.serve("172.21.0.7", "172.21.0.6", tmp_path / "grant.sqlite", secret_fd=4)
    with pytest.raises(ValueError, match="nonroot_bounded_stdin_staging_only"):
        ns.serve("172.21.0.7", "172.21.0.6", tmp_path / "grant.sqlite", max_seconds=40)


def test_timeout_forces_child_termination(monkeypatch):
    class Pipe:
        def __init__(self):
            self.closed = False

        def poll(self, t):
            return False

        def close(self):
            self.closed = True

    class Child:
        def __init__(self):
            self.terminated = False
            self.alive = False
            self.exitcode = None

        def start(self):
            self.alive = True

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False
            self.exitcode = -15

        def kill(self):
            pytest.fail("must terminate before kill")

    class Ctx:
        def __init__(self):
            self.rd = Pipe()
            self.wr = Pipe()
            self.child = Child()

        def Pipe(self, *, duplex):
            assert not duplex
            return self.rd, self.wr

        def Process(self, *, target, args, daemon):
            assert target == ns._model_child and args == (self.wr,) and daemon
            return self.child

    ctx = Ctx()
    monkeypatch.setattr(ns.multiprocessing, "get_context", lambda name: ctx)
    with pytest.raises(ValueError, match="bounded_namespaced_model_failed"):
        ns.run_bounded_namespaced_inference()
    assert ctx.child.terminated
    assert ctx.rd.closed and ctx.wr.closed
    assert ns.MAX_CHILD_SECONDS * 1000 < ns.signed_slot.MAX_SLOT_MS


def test_operator_cli_defaults_to_no_docker_or_network(tmp_path):
    import subprocess

    path = SCRIPTS / "fleet_lfm3_namespaced_operator_probe.py"
    result = subprocess.run([sys.executable, str(path)], cwd=tmp_path, text=True, capture_output=True, timeout=5)
    assert result.returncode == 2
    assert "EXPLICIT_OPERATOR_ONLY" in result.stdout
    assert list(tmp_path.iterdir()) == []
