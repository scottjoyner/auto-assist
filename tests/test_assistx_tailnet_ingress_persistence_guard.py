"""Fail-closed tests for read-only AssistX ingress reboot persistence checks."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

# CI may already import an unrelated namespace package named `scripts`.
# Load the exact local file rather than resolving a repository-ambiguous name.
GUARD_FILE=Path(__file__).resolve().parents[1]/"scripts/assistx_tailnet_ingress_persistence_guard.py"
GUARD_SPEC=importlib.util.spec_from_file_location("assistx_tailnet_ingress_guard_test",GUARD_FILE)
assert GUARD_SPEC is not None and GUARD_SPEC.loader is not None
guard=importlib.util.module_from_spec(GUARD_SPEC)
GUARD_SPEC.loader.exec_module(guard)

BASE=dict(
    raw_active=False,raw_enabled=False,raw_unit_matches=True,
    backend_loopback_only=True,tailnet_listener=True,local_listener=True,
    unknown_8000_listener=False,caddyfile_pinned=True,
    binary_pinned=True,serve_api_proxy_routes=5,
)

def decide(**overrides):
    params={**BASE,**overrides}
    return guard.evaluate(**params)

def test_default_never_observes_or_changes_fleet():
    result=subprocess.run([sys.executable,str(Path(guard.__file__))],
                          check=True,capture_output=True,text=True,timeout=8)
    data=json.loads(result.stdout)
    assert data["decision"]=="HOLD"
    assert data["physical_executed"] is False
    assert data["production_mutations"]==0
    assert data["automatic_cutover_or_rollback_authorized"] is False

@pytest.mark.parametrize("changes,reason",[
    ({"raw_active":True,"raw_enabled":True},"RAW_FORWARDER_BOOT_PERSISTENT"),
    ({"raw_active":True,"raw_enabled":False},"RAW_FORWARDER_CURRENTLY_ACTIVE"),
    ({"raw_active":False,"raw_enabled":True},"UNSAFE_SOCAT_RESTART_ON_BOOT"),
    ({"tailnet_listener":False},"FAIL_CLOSED_NO_TAILNET_INGRESS"),
    ({"backend_loopback_only":False},"UNKNOWN_EXPOSURE"),
    ({"unknown_8000_listener":True},"UNKNOWN_EXPOSURE"),
    ({}, "CADDY_INGRESS_NOT_YET_AUTHENTICATED"),
])
def test_never_grants_release_when_boot_path_unsafe(changes,reason):
    report=decide(**changes)
    assert report["reason"]==reason
    assert report["decision"]=="HOLD"
    assert report["automatic_cutover_or_rollback_authorized"] is False
    assert report["production_mutations"]==0

def test_caddy_integrity_not_sufficient_for_release():
    for values in ({"caddyfile_pinned":False},
                   {"binary_pinned":False},
                   {"serve_api_proxy_routes":0}):
        report=decide(**values)
        assert report["decision"]=="HOLD"
        assert report["postcutover_real_client_acceptance"] is False
    assert decide()["separate_tailscale_serve_api_routes"]==5

@pytest.mark.parametrize("value",[-1,None,"5",2.5])
def test_invalid_serve_counts_fail_closed(value):
    with pytest.raises(guard.ObservationFailed):
        decide(serve_api_proxy_routes=value)

def test_socket_parser_preserves_bound_interfaces():
    source=("LISTEN 0 5 100.64.43.123:8000 0.0.0.0:*\n"
            "LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*\n"
            "LISTEN 0 4096 100.64.43.123:8443 0.0.0.0:*\n")
    assert guard.listen_endpoints(source)==[
        "100.64.43.123:8000","127.0.0.1:8000","100.64.43.123:8443"]
    with pytest.raises(guard.ObservationFailed):
        guard.listen_endpoints("LISTEN malformed")

def test_physical_instrumentation_is_inspection_only():
    code=Path(guard.__file__).read_text()
    for literal in ('"systemctl","--user","is-enabled"',
                    '"systemctl","--user","is-active"',
                    '"tailscale","serve","status","--json"',
                    '"docker","inspect"','"ss","-H","-lnt"',
                    'sha256_file(CADDYFILE)','sha256_file(Path("/usr/bin/caddy"))'):
        assert literal in code
    for forbidden in ('systemctl stop','systemctl disable',
                      'systemctl restart','docker stop','docker restart',
                      'tailscale serve --bg','open("/etc',
                      'method="POST"','/api/tasks','print(password)'):
        assert forbidden not in code
    assert '"credential_values_collected":False' in code
    assert '"automatic_cutover_or_rollback_authorized":False' in code


BOOT_READY=dict(
    live_caddy_static_verified=True,raw_service_masked=True,
    one_boot_enabled_caddy=True,user_manager_linger=True,
)


def test_pinned_fallback_boot_configured_does_not_certify_reboot():
    outcome=decide(**BOOT_READY)
    assert outcome["reason"]=="PINNED_SANITIZER_BOOT_CONFIGURED_REBOOT_NOT_WITNESSED"
    assert outcome["pinned_caddy_live_static_verified"] is True
    assert outcome["legacy_socat_masked"] is True
    assert outcome["exactly_one_boot_enabled_caddy"] is True
    assert outcome["user_systemd_linger_enabled"] is True
    assert outcome["real_reboot_survival_verified"] is False
    assert outcome["postcutover_real_client_acceptance"] is False
    assert outcome["automatic_cutover_or_rollback_authorized"] is False
    assert outcome["decision"]=="HOLD"


@pytest.mark.parametrize("bad",(
    {"live_caddy_static_verified":False},
    {"raw_service_masked":False},
    {"one_boot_enabled_caddy":False},
    {"caddyfile_pinned":False},
    {"binary_pinned":False},
    {"local_listener":False},
    {"serve_api_proxy_routes":4},
    {"serve_api_proxy_routes":6},
))
def test_missing_runtime_or_boot_evidence_is_not_persistence_attestation(bad):
    outcome=decide(**{**BOOT_READY,**bad})
    assert outcome["reason"]!="PINNED_SANITIZER_BOOT_CONFIGURED_REBOOT_NOT_WITNESSED"
    assert outcome["decision"]=="HOLD"
    assert outcome["real_reboot_survival_verified"] is False


def test_no_user_linger_is_explicit_boot_failure():
    outcome=decide(**{**BOOT_READY,"user_manager_linger":False})
    assert outcome["reason"]=="SANITIZER_USER_MANAGER_NOT_PERSISTENT"
    assert outcome["automatic_cutover_or_rollback_authorized"] is False


@pytest.mark.parametrize("bad,expected",(
    ({"raw_active":True},"RAW_FORWARDER_CURRENTLY_ACTIVE"),
    ({"raw_enabled":True},"UNSAFE_SOCAT_RESTART_ON_BOOT"),
    ({"tailnet_listener":False},"FAIL_CLOSED_NO_TAILNET_INGRESS"),
    ({"unknown_8000_listener":True},"UNKNOWN_EXPOSURE"),
    ({"backend_loopback_only":False},"UNKNOWN_EXPOSURE"),
))
def test_unsafe_live_state_overrides_healthy_boot_configuration(bad,expected):
    report=decide(**{**BOOT_READY,**bad})
    assert report["reason"]==expected
    assert report["decision"]=="HOLD"


@pytest.mark.parametrize("flag",tuple(BOOT_READY))
def test_nonboolean_caddy_attestations_denied(flag):
    with pytest.raises(guard.ObservationFailed):
        decide(**{**BOOT_READY,flag:"true"})


def test_import_is_pinned_local_file_not_ambiguous_installed_module():
    source=Path(guard.__file__).read_text()
    assert 'ROOT/"scripts/assistx_tailnet_postcutover_attestation.py"' in source
    assert 'importlib.util.spec_from_file_location' in source
    assert 'postcutover.physical(live[0])' in source
    assert 'len(live)==len(boot)==1 and live==boot' in source
    assert '"loginctl","show-user","scott","--property=Linger"' in source
    assert '"user_systemd_linger_enabled":user_manager_linger' in source
    for forbidden in (
        "systemctl --user restart", "systemctl --user stop",
        "loginctl enable-linger", "tailscale serve --bg",
        "docker restart", "docker exec", "method=\"POST\"",
    ):
        assert forbidden not in source
