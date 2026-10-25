"""Fail-closed tests for separate mobile SSO Serve path ownership."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE=Path(__file__).resolve().parents[1]/"scripts/assistx_mobile_serve_scope_guard.py"
SPEC=importlib.util.spec_from_file_location("assistx_mobile_scope_test",SOURCE)
assert SPEC is not None and SPEC.loader is not None
guard=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

HOST="x1-370.tailcb8954.ts.net"
PORT=HOST+":8443"

def fixture():
    web={
        PORT:{"Handlers":{
            "/":{"Proxy":"http://127.0.0.1:8081"},
            "/relay/signals":{"Proxy":"http://127.0.0.1:8790"},
            **{path:{"Proxy":"http://127.0.0.1:8000"+path}
               for path in guard.EXPECTED_PATHS},
        }},
        HOST+":8445":{"Handlers":{
            "/":{"Proxy":"http://127.0.0.1:8781"}}},
    }
    return {"Web":web}

def status(serve=None,header=True,allowlist=True):
    return guard.classify(
        fixture() if serve is None else serve,HOST,header,allowlist)

def test_default_inert():
    p=subprocess.run([sys.executable,str(SOURCE)],
                     capture_output=True,text=True,check=True,timeout=8)
    value=json.loads(p.stdout)
    assert value["decision"]=="HOLD"
    assert value["physical_executed"] is False
    assert value["production_mutations"]==0

def test_preserves_unrelated_serve_roots_and_exact_mobile_contract():
    report=status()
    assert report["route_count"]==5
    assert report["serve_mobile_routes_exact"] is True
    assert report["unrelated_routes_preserved"] is True
    assert report["global_legacy_auth_setting_safe_to_change"] is False
    assert report["decision"]=="HOLD"

def test_absent_mobile_route_never_reports_exact():
    doc=fixture()
    del doc["Web"][PORT]["Handlers"]["/api/v1/auth/whoami"]
    report=status(doc)
    assert report["serve_mobile_routes_exact"] is False
    assert report["route_count"]==4
    assert report["decision"]=="HOLD"

@pytest.mark.parametrize("rewrite",[
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8000/api/tasks",
    "http://127.0.0.1:8100/api/v1/auth/whoami",
])
def test_wrong_mobile_route_target_denied(rewrite):
    doc=fixture()
    doc["Web"][PORT]["Handlers"]["/api/v1/auth/whoami"]={"Proxy":rewrite}
    with pytest.raises(guard.Rejected):
        status(doc)

@pytest.mark.parametrize("route",["/","/api/tasks","/api/degraded/status"])
def test_wider_api_route_rejected(route):
    doc=fixture()
    doc["Web"][PORT]["Handlers"][route]={"Proxy":"http://127.0.0.1:8000"+route}
    with pytest.raises(guard.Rejected):
        status(doc)

def test_other_host_or_port_cannot_expose_api():
    doc=fixture()
    doc["Web"][HOST+":8449"]={"Handlers":{"/api/tasks":{
        "Proxy":"http://127.0.0.1:8000/api/tasks"}}}
    with pytest.raises(guard.Rejected):
        status(doc)

@pytest.mark.parametrize("host",["x1-370.tailcb8954.ts.net:8000",
                                 "x1-370.tailcb8954.ts.net",
                                 "wrong-host.local:8443"])
def test_wrong_host_metadata_is_denied(host):
    doc=fixture()
    doc["Web"][host]={"Handlers":{
        "/api/v1/auth/whoami":{"Proxy":"http://127.0.0.1:8000/api/v1/auth/whoami"}}}
    with pytest.raises(guard.Rejected):
        status(doc)

def test_missing_identity_configuration_or_allowlist_cannot_mean_release():
    for h,a in ((False,True),(True,False),(False,False)):
        value=status(header=h,allowlist=a)
        assert value["decision"]=="HOLD"
        assert value["tailscale_login_header_canonical"] is h
        assert value["mobile_allowlist_available"] is a
        assert value["live_mobile_client_accepted"] is False

def test_no_credentials_api_writes_or_mutating_serve_calls():
    raw=SOURCE.read_text()
    assert '"tailscale","serve","status","--json"' in raw
    assert '"docker","inspect"' in raw
    assert '"global_legacy_auth_setting_safe_to_change":False' in raw
    for forbidden in ("systemctl restart","systemctl stop","tailscale serve --bg",
                      "docker restart","docker exec","/api/tasks/claim",
                      "print(value)",'"method":"POST"'):
        assert forbidden not in raw
