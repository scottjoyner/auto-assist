"""Adversarial unit controls for read-only Docker image custody."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/read_only_image_custody.py"
SPEC = importlib.util.spec_from_file_location("image_custody_under_test", PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

SECRET = "very-sensitive-token-DO-NOT-LEAK"
IMG = "sha256:" + "a" * 64
DAEMON = "420f0b3b-0b94-4ae1-b266-717cbb337f49"


def container(name: str) -> dict:
    return {
        "Name": "/" + name, "Id": "f" * 64, "Image": IMG,
        "State": {
            "Status": "running",
            "StartedAt": "2026-10-17T15:55:28.936803265Z",
            "Pid": 4531,
        },
        "NetworkSettings": {"Networks": {"synthetic-net": {"IPAddress": SECRET}}},
        "Mounts": [
            {"Type": "volume", "RW": True, "Source": "/secret/" + SECRET},
            {"Type": "bind", "RW": False, "Source": "/private/" + SECRET},
        ],
        "Config": {"Env": ["SECRET=" + SECRET, "NEO4J_USER=" + SECRET]},
    }


def image() -> dict:
    return {
        "Id": IMG,
        "RepoDigests": ["private.registry/internal@sha256:" + "b" * 64],
        "Config": {
            "Env": ["TOKEN=" + SECRET],
            "Labels": {"org.opencontainers.image.revision": "c" * 40,
                       "private-label": SECRET},
        },
    }


def stub(monkeypatch, *, change=None, daemon=None, context="default", socket="unix:///var/run/docker.sock"):
    calls = []
    def read(argv):
        calls.append(tuple(argv))
        if argv == ["docker", "context", "show"]:
            return context
        if argv == [
            "docker", "context", "inspect", "default",
            "--format", "{{.Endpoints.docker.Host}}",
        ]:
            return socket
        if argv == ["docker", "info", "--format", "{{.ID}}"]:
            if daemon is not None:
                return daemon(len([x for x in calls if x[:2] == ("docker", "info")]))
            return DAEMON
        if argv[:3] == ["docker", "container", "inspect"]:
            name = argv[3]
            assert name in audit.CONTAINERS
            value = container(name)
            if change:
                change("container", name, value, len(calls))
            return json.dumps([value])
        if argv[:3] == ["docker", "image", "inspect"]:
            assert argv[3] == IMG
            value = image()
            if change:
                change("image", argv[3], value, len(calls))
            return json.dumps([value])
        pytest.fail("non-read-only or foreign Docker command")
    monkeypatch.setattr(audit, "_run", read)
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH", "DOCKER_CONFIG"):
        monkeypatch.delenv(key, raising=False)
    return calls


def test_consistent_inspection_sees_both_image_and_container_no_source_proof(monkeypatch):
    calls = stub(monkeypatch)
    result = audit.observe()
    encoded = json.dumps(result)
    assert result["production_decision"] == "HOLD"
    assert result["promotion_authorized"] is False
    assert result["signed_source_to_image_verified"] is False
    assert result["two_pass_identity_stable"]
    assert len(result["containers"]) == len(audit.CONTAINERS)
    assert all(x["image"]["local_image_id"] == IMG for x in result["containers"])
    assert all(x["image"]["manifest_digest_values"] == ["sha256:" + "b"*64]
               for x in result["containers"])
    assert all(x["image"]["unsigned_revision_claim"] == "c"*40
               for x in result["containers"])
    assert SECRET not in encoded
    assert "private.registry" not in encoded
    assert "/secret" not in encoded
    assert len([c for c in calls if c[:3] == ("docker","container","inspect")]) == 10
    assert len([c for c in calls if c[:3] == ("docker","image","inspect")]) == 10
    assert all(c[:3] in (
        ("docker", "container", "inspect"),
        ("docker", "image", "inspect"),
        ("docker", "info", "--format"),
        ("docker", "context", "show"),
        ("docker", "context", "inspect"),
    ) for c in calls)


def test_no_repository_digest_never_upgrades_attestation(monkeypatch):
    def change(kind, _name, obj, _n):
        if kind == "image":
            obj["RepoDigests"] = []
    stub(monkeypatch, change=change)
    result = audit.observe()
    assert "NO_MANIFEST_DIGEST:assistx-api" in result["findings"]
    assert not result["signed_source_to_image_verified"]


@pytest.mark.parametrize("mutation", [
    lambda d: d.update({"Id": "sha256:" + "f"*64}),
    lambda d: d.update({"RepoDigests": [SECRET]}),
    lambda d: d.update({"RepoDigests": [42]}),
    lambda d: d.update({"RepoDigests": "latest"}),
])
def test_corrupt_image_identity_or_manifest_is_denied(monkeypatch, mutation):
    def change(kind, _name, obj, _n):
        if kind == "image":
            mutation(obj)
    stub(monkeypatch, change=change)
    with pytest.raises(audit.CustodyHold):
        audit.observe()


@pytest.mark.parametrize("field", ["Id", "Image", "Name", "Mounts", "State", "NetworkSettings"])
def test_container_identity_and_shape_denied(monkeypatch, field):
    def change(kind, _name, obj, _n):
        if kind != "container":
            return
        obj[field] = {
            "Id": "invalid", "Image": "latest", "Name": "/wrong",
            "Mounts": "not-list", "State": {"Status": "unknown"},
            "NetworkSettings": {"Networks": None},
        }[field]
    stub(monkeypatch, change=change)
    with pytest.raises(audit.CustodyHold):
        audit.observe()


@pytest.mark.parametrize("drift", ["pid", "network", "image_revision", "start", "mount"])
def test_two_pass_catches_runtime_or_image_drift(monkeypatch, drift):
    visits = {"count": 0}
    def change(kind, name, obj, _n):
        if kind == "container" and name == "assistx-api":
            visits["count"] += 1
            if visits["count"] != 2:
                return
            if drift == "pid":
                obj["State"]["Pid"] = 9007
            if drift == "network":
                obj["NetworkSettings"]["Networks"] = {"second-net": {}}
            if drift == "start":
                obj["State"]["StartedAt"] = "2026-10-17T16:25:28Z"
            if drift == "mount":
                obj["Mounts"][0]["RW"] = False
        if kind == "image" and drift == "image_revision":
            visits["count"] += 1
            if visits["count"] > 2:
                obj["Config"]["Labels"]["org.opencontainers.image.revision"] = "e" * 40
    stub(monkeypatch, change=change)
    with pytest.raises(audit.CustodyHold, match="drift"):
        audit.observe()


def test_change_of_daemon_identity_between_passes_denies(monkeypatch):
    stub(monkeypatch, daemon=lambda n: DAEMON if n < 3 else "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    with pytest.raises(audit.CustodyHold, match="daemon changed"):
        audit.observe()


def test_nondefault_context_denied_without_any_container_reads(monkeypatch):
    calls = stub(monkeypatch, context="remote")
    with pytest.raises(audit.CustodyHold, match="nonlocal"):
        audit.observe()
    assert not any(x[:3] == ("docker", "container", "inspect") for x in calls)


@pytest.mark.parametrize("env", [
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH", "DOCKER_CONFIG",
])
def test_override_denied_before_first_docker_call(monkeypatch, env):
    calls = stub(monkeypatch)
    monkeypatch.setenv(env, "custom")
    with pytest.raises(audit.CustodyHold, match="override"):
        audit.observe()
    assert calls == []


def test_wrong_name_outside_fixed_allowlist_denied(monkeypatch):
    calls = stub(monkeypatch)
    with pytest.raises(audit.CustodyHold, match="allowlist"):
        audit._container("arbitrary-secret-container")
    assert calls == []


def test_docker_command_failure_never_prints_stderr_or_env(monkeypatch):
    def fail(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout=SECRET, stderr="pwd=" + SECRET)
    monkeypatch.setattr(audit.subprocess, "run", fail)
    with pytest.raises(audit.CustodyHold) as exc:
        audit._run(["docker", "context", "show"])
    assert SECRET not in str(exc.value)


def test_json_cli_fail_closed_when_docker_read_fails(monkeypatch, capsys):
    monkeypatch.setattr(audit, "observe", lambda: (_ for _ in ()).throw(
        audit.CustodyHold("no Docker identity")
    ))
    assert audit.main(["--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["production_decision"] == "HOLD"
    assert payload["promotion_authorized"] is False

@pytest.mark.parametrize("socket", [
    "tcp://evil.example:2375",
    "ssh://remote-docker",
    "unix:///home/scott/fake-daemon.sock",
    "npipe:////./pipe/docker_engine",
])
def test_default_named_context_with_foreign_endpoint_refused(monkeypatch, socket):
    calls = stub(monkeypatch, socket=socket)
    with pytest.raises(audit.CustodyHold, match="socket"):
        audit.observe()
    assert not any(c[:3] == ("docker", "container", "inspect") for c in calls)
