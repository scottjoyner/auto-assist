"""No live Docker/network/credential access; synthetic router alias migration gate."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

# Some installed dependencies also expose a top-level "scripts" package.
# Load this specific checked-out module by path: the proof must never
# accidentally test a third-party module during hosted pytest collection.
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "assistx_router_alias_preflight_under_test",
    ROOT / "scripts" / "audit_router_alias_preflight.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTAINER = MODULE.CONTAINER
IDENTITY_TEMPLATE = MODULE.IDENTITY_TEMPLATE
RUNTIME_PROBE = MODULE.RUNTIME_PROBE
HERMES_PROBE = MODULE.HERMES_PROBE
HERMES_CONTAINER = MODULE.HERMES_CONTAINER
SETTINGS = MODULE.SETTINGS
audit = MODULE.audit
compose_defaults_pinned = MODULE.compose_defaults_pinned
container_identity = MODULE.container_identity
docker_read = MODULE.docker_read
evaluate = MODULE.evaluate
write_private = MODULE.write_private

IMAGE = "sha256:" + "a" * 64
IDENTITY = IMAGE + "|2026-11-04T09:15:05Z|true"


def fixture_output(*, base=True, fleet=True, alias_dns=True,
                   tcp=True, health=200, userinfo=False) -> dict:
    return {
        "settings": {
            "AUTO_ROUTER_BASE_URL": {
                "configured": True,
                "canonical_internal_router_url": base,
                "embedded_userinfo": userinfo,
            },
            "FLEET_ROUTER_URL": {
                "configured": True,
                "canonical_internal_router_url": fleet,
                "embedded_userinfo": False,
            },
        },
        "router_alias_dns_resolves": alias_dns,
        "router_alias_tcp_reachable": tcp,
        "router_alias_health_http_status": health,
        "credentials_or_headers_sent": False,
        "response_bodies_read": False,
        "production_mutation": False,
        "hermes": {
            "configured": True,
            "canonical_internal_router_v1_url": True,
            "embedded_userinfo": False,
            "router_alias_dns_resolves": True,
            "router_alias_tcp_reachable": True,
            "router_alias_health_http_status": 200,
            "credentials_or_headers_sent": False,
            "response_bodies_read": False,
            "production_mutation": False,
            "identity_stable": True,
        },
    }


def fake_reader(response: dict, *, restarted=False, hermes_restarted=False):
    calls = []
    counts = {CONTAINER: 0, HERMES_CONTAINER: 0}

    def read(argv):
        calls.append(tuple(argv))
        for container in counts:
            if argv == ["docker", "inspect", "--format", IDENTITY_TEMPLATE, container]:
                counts[container] += 1
                if ((container == CONTAINER and restarted)
                        or (container == HERMES_CONTAINER and hermes_restarted)):
                    if counts[container] > 1:
                        return IMAGE + "|2026-11-04T09:16:05Z|true"
                return IDENTITY
        if argv == ["docker", "exec", CONTAINER, "python", "-B", "-c", RUNTIME_PROBE]:
            return json.dumps(response)
        if argv == ["docker", "exec", HERMES_CONTAINER, "python", "-B", "-c", HERMES_PROBE]:
            return json.dumps({k: v for k, v in response["hermes"].items()
                               if k != "identity_stable"})
        raise AssertionError("unapproved Docker call: " + str(argv))
    return read, calls


def test_compose_default_alias_pins_are_correct():
    assert compose_defaults_pinned(ROOT)
    text = (ROOT / "docker-compose.yml").read_text()
    for key in SETTINGS:
        expected = key + "=" + "$" + "{" + key + ":-http://llm-router:8088}"
        assert text.count(expected) == 1


def test_proposed_verified_runtime_is_preflight_only_not_cutover():
    read, calls = fake_reader(fixture_output())
    result = audit(read=read)
    # Both API URLs and Hermes health may look good in this synthetic
    # fixture, but isolation-preserving Hermes relay is not yet reviewed.
    assert result["gate"] == "HOLD"
    assert all(v for k, v in result["checks"].items()
               if k != "hermes_scoped_private_relay_reviewed")
    assert result["checks"]["hermes_scoped_private_relay_reviewed"] is False
    assert result["production_ready"] is False
    assert result["host_port_isolation_approved"] is False
    assert result["container_recreation_authorized"] is False
    assert result["credential_values_accessed"] is False
    assert all(args[:2] in (("docker", "inspect"), ("docker", "exec")) for args in calls)


@pytest.mark.parametrize(("base", "fleet"), [
    (False, True),
    (True, False),
    (False, False),
])
def test_real_current_external_and_unresolvable_default_hold(base, fleet):
    receipt = evaluate(IDENTITY, IDENTITY, fixture_output(base=base, fleet=fleet),
                       compose_defaults_correct=True)
    assert receipt["gate"] == "HOLD"
    assert not receipt["checks"]["both_runtime_urls_use_router_alias"]
    assert receipt["production_ready"] is False


@pytest.mark.parametrize(("key", "value"), [
    ("router_alias_dns_resolves", False),
    ("router_alias_tcp_reachable", False),
    ("router_alias_health_http_status", None),
])
def test_internal_dns_transport_or_health_failure_holds(key, value):
    response = fixture_output()
    response[key] = value
    assert evaluate(IDENTITY, IDENTITY, response,
                    compose_defaults_correct=True)["gate"] == "HOLD"


def test_username_or_password_in_url_denies():
    r = evaluate(IDENTITY, IDENTITY, fixture_output(userinfo=True),
                 compose_defaults_correct=True)
    assert r["gate"] == "HOLD"
    assert not r["checks"]["no_embedded_url_credentials"]


@pytest.mark.parametrize(("key", "value"), [
    ("canonical_internal_router_v1_url", False),
    ("configured", False),
    ("embedded_userinfo", True),
    ("router_alias_dns_resolves", False),
    ("router_alias_tcp_reachable", False),
    ("router_alias_health_http_status", None),
    ("identity_stable", False),
])
def test_hermes_private_router_url_or_transport_failure_blocks_combined_preflight(key, value):
    result = fixture_output()
    result["hermes"][key] = value
    proof = evaluate(IDENTITY, IDENTITY, result, compose_defaults_correct=True)
    assert proof["gate"] == "HOLD"
    assert proof["production_ready"] is False


def test_missing_or_extra_hermes_evidence_rejected():
    result = fixture_output()
    del result["hermes"]
    with pytest.raises(ValueError, match="Hermes runtime proof missing"):
        evaluate(IDENTITY, IDENTITY, result, compose_defaults_correct=True)
    result = fixture_output()
    result["hermes"]["secrets"] = "should-never-be-in-receipt"
    with pytest.raises(ValueError, match="unexpected Hermes"):
        evaluate(IDENTITY, IDENTITY, result, compose_defaults_correct=True)


def test_hermes_container_identity_change_forces_hold():
    read, _ = fake_reader(fixture_output(), hermes_restarted=True)
    result = audit(read=read)
    assert result["gate"] == "HOLD"
    assert not result["checks"]["hermes_adapter_identity_stable"]


def test_source_default_without_network_attachment_denies(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "\n".join(
            key + "=" + "$" + "{" + key + ":-http://llm-router:8088}"
            for key in SETTINGS
        ) + "\n"
    )
    assert not compose_defaults_pinned(tmp_path)
    read, _ = fake_reader(fixture_output())
    r = audit(read=read, root=tmp_path)
    assert r["gate"] == "HOLD"
    assert not r["checks"]["compose_defaults_use_router_alias"]


def test_hermes_adapter_isolation_is_not_weakened_in_compose():
    source = (ROOT / "docker-compose.yml").read_text()
    section = source.split("  hermes-adapter:", 1)[1].split("\nvolumes:", 1)[0]
    assert "HERMES_LM_BASE_URL=${HERMES_LM_BASE_URL:-http://host.docker.internal:8088/v1}" in section
    # The adapter intentionally does not join the router bridge; CI's
    # release-verify-compose policy must remain unmodified and passing.
    assert "      - git_default" not in section
    assert "    networks:" not in section
    read, _ = fake_reader(fixture_output())
    proof = audit(read=read)
    assert proof["gate"] == "HOLD"
    assert proof["checks"]["hermes_scoped_private_relay_reviewed"] is False


def test_docker_instance_replacement_denied():
    read, _ = fake_reader(fixture_output(), restarted=True)
    with pytest.raises(ValueError, match="restarted"):
        audit(read=read)


@pytest.mark.parametrize("identity", [
    "sha256:" + "b" * 63 + "|now|true",
    "unsafe:tag|now|true",
    IMAGE + "|now|false",
    IMAGE + "|now",
    IMAGE + "||true",
])
def test_running_container_identity_must_be_immutable_and_complete(identity):
    with pytest.raises(ValueError):
        container_identity(lambda command: identity)


@pytest.mark.parametrize("malformed", [
    {},
    {"settings": {}},
    {"settings": {"AUTO_ROUTER_BASE_URL": {}}},
])
def test_missing_runtime_proof_denied(malformed):
    with pytest.raises(ValueError):
        evaluate(IDENTITY, IDENTITY, malformed, compose_defaults_correct=True)


def test_unexpected_probe_side_effect_or_secret_field_denied():
    r = fixture_output()
    r["credentials_or_headers_sent"] = True
    with pytest.raises(ValueError):
        evaluate(IDENTITY, IDENTITY, r, compose_defaults_correct=True)
    assert "AUTO_ROUTER_ADMIN_TOKEN" not in RUNTIME_PROBE + HERMES_PROBE
    assert "FLEET_ROUTER_BEARER_TOKEN" not in RUNTIME_PROBE + HERMES_PROBE
    assert "HERMES_LM_API_KEY" not in HERMES_PROBE
    assert "'/admin/" not in HERMES_PROBE
    assert "'POST'" not in HERMES_PROBE
    assert ".read(" not in HERMES_PROBE
    assert 'getresponse().status' in RUNTIME_PROBE
    assert '.read(' not in RUNTIME_PROBE
    assert "'/admin/" not in RUNTIME_PROBE
    assert "'POST'" not in RUNTIME_PROBE


def test_docker_read_command_allowlist_blocks_mutations():
    for args in (
        ["docker", "restart", CONTAINER],
        ["docker", "network", "disconnect", "git_default", CONTAINER],
        ["docker", "exec", CONTAINER, "sh", "-c", "env"],
        ["docker", "exec", CONTAINER, "python", "-c", "print('secret')"],
    ):
        with pytest.raises(ValueError):
            docker_read(args)


def test_private_receipt_written_once_and_not_arbitrary_path(tmp_path):
    target = Path("/tmp") / f"assistx-router-alias-unit-{os.getpid()}.json"
    try:
        write_private(target, {"gate": "HOLD", "production_ready": False})
        assert target.stat().st_mode & 0o777 == 0o600
        assert json.loads(target.read_text())["gate"] == "HOLD"
        with pytest.raises(ValueError):
            write_private(target, {"gate": "PASS"})
    finally:
        target.unlink(missing_ok=True)
    with pytest.raises(ValueError):
        write_private(tmp_path / "assistx-router-alias-unit.json", {"gate": "HOLD"})


def test_no_explicit_cli_optin_cannot_touch_docker():
    result = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.audit_router_alias_preflight"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=4,
    )
    assert result.returncode != 0
    assert "--explicit-read-only" in result.stderr
    assert not result.stdout
