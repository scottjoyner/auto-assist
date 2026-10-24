"""Inert and fail-closed tests for actual live off-host sanitized ingress audit.

All unit tests are offline; only explicit physical CLI may contact Lenovo or
the live AssistX protected read-only control-room endpoint.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import assistx_tailnet_live_offhost_acceptance_canary as live


def test_default_inert_with_no_network_or_credentials():
    result=subprocess.run(
        [sys.executable,str(Path(live.__file__))],
        capture_output=True,text=True,timeout=7,check=True)
    data=json.loads(result.stdout)
    assert data=={"decision":"HOLD","physical_executed":False,
                  "real_api_task_mutations":0}


@pytest.mark.parametrize("gate,address",[
    (False,"100.64.43.123"),
    (None,"100.64.43.123"),
    (True,"100.64.43.124"),
    (True,"127.0.0.1"),
])
def test_sock_to_live_listener_requires_verified_pinned_caddy(gate,address):
    with pytest.raises(live.Denied):
        live.live_socket_through_lenovo(
            19800,address,verified_gate=gate)


@pytest.mark.parametrize("path",[
    "/api/tasks","/api/executor/claims/x/status",
    "/command-center","/admin","/control-room?a=1",
])
def test_only_approved_readonly_routes_allowed(path):
    with pytest.raises(live.Denied):
        live.status_via_lenovo(
            19801,"100.64.43.123",
            path=path,verified_gate=True)


def test_live_status_parser_uses_canonical_not_redirect_route():
    source=Path(live.__file__).read_text()
    assert 'path="/control-room"' in source
    assert '("/health","/control-room")' in source
    assert 'path="/command-center"' not in source
    assert 'GET {path} HTTP/1.1' in source
    assert "read(0)" not in source
    assert '"task_mutations":1' not in source


def test_live_scope_checks_pin_service_cgroup_and_staged_assets():
    source=Path(live.__file__).read_text()
    assert 'unit_state' not in source
    assert 'service_owns_listener(' in source
    assert '"ControlGroup"' in source
    assert 'attest.mobile_serve_paths_exact(' in source
    assert 'BIN_SHA' in source and 'EXPECTED_SHA' in source
    assert 'unit_exec=run([' in source
    assert '"--property=ExecStart"' in source
    assert 'RAW="control-room-ts-proxy.service"' in source
    assert 'not in ("enabled","enabled-runtime")' in source
    assert 'len(choices)==1' in source


def test_auth_credentials_never_leave_x1_and_mutations_are_absent():
    source=Path(live.__file__).read_text()
    assert 'Basic "+base64.b64encode(' in source
    assert 'offhost.start_tunnel(' in source
    assert 'offhost.stop(ssh)' in source
    assert 'production_hermes_unchanged' in source
    assert '"real_api_task_mutations":0' in source
    assert '"private_credentials_logged_or_stored":False' in source
    for forbidden in (
        'docker restart','docker stop','docker exec',
        'systemctl restart','systemctl stop','systemctl disable',
        'systemctl enable','tailscale serve --bg',
        'method="POST"', "password).decode())\n    print",
    ):
        assert forbidden not in source


def test_offhost_expected_statuses_fail_closed(monkeypatch):
    sequence=[200,401,401,200,200]
    fake=SimpleNamespace(poll=lambda:None)
    monkeypatch.setattr(live,"trusted_ingress_state",
                        lambda:("100.64.43.123",live.FALLBACK))
    monkeypatch.setattr(live,"basic_in_memory",lambda:"Basic synthetic-only")
    monkeypatch.setattr(live.offhost,"free_port",lambda:19045)
    monkeypatch.setattr(live.offhost,"start_tunnel",lambda port:fake)
    monkeypatch.setattr(live.offhost,"stop",lambda proc:None)
    monkeypatch.setattr(live,"status_via_lenovo",
        lambda *args,**kwargs:sequence.pop(0))
    monkeypatch.setattr(live,"run",lambda args:SimpleNamespace(
        returncode=0,stdout=live.HERMES_SHA+" true 16"))
    result=live.physical()
    assert result["offhost_forged_401"] is True
    assert result["offhost_basic_200"] is True
    assert result["old_socat_not_active_or_boot_enabled"]
    assert result["real_api_task_mutations"]==0


def test_offhost_authentication_mismatch_is_denied_and_ssh_stopped(monkeypatch):
    sequence=[200,401,401,401,200]
    stopped=[]
    fake=SimpleNamespace(poll=lambda:None)
    monkeypatch.setattr(live,"trusted_ingress_state",
                        lambda:("100.64.43.123",live.FALLBACK))
    monkeypatch.setattr(live,"basic_in_memory",lambda:"Basic synthetic-only")
    monkeypatch.setattr(live.offhost,"free_port",lambda:19045)
    monkeypatch.setattr(live.offhost,"start_tunnel",lambda port:fake)
    monkeypatch.setattr(live.offhost,"stop",lambda proc:stopped.append(proc))
    monkeypatch.setattr(live,"status_via_lenovo",
        lambda *args,**kwargs:sequence.pop(0))
    with pytest.raises(live.Denied,match="off-host auth or forgery"):
        live.physical()
    assert stopped==[fake]
