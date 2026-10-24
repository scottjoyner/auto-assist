"""Fail-closed, disposable off-host sanitizer ingress qualification tests."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
import assistx_tailnet_lenovo_ingress_canary as test_runtime


def test_default_is_inert_and_does_not_start_listener():
    result=subprocess.run([sys.executable,str(Path(test_runtime.__file__))],
                          text=True,capture_output=True,timeout=8,check=True)
    data=json.loads(result.stdout)
    assert data["decision"]=="HOLD"
    assert data["physical_executed"] is False
    assert data["real_port_8000_cutover_authorized"] is False
    assert data["production_proxy_or_container_restarts"]==0


def test_tailnet_binding_only_and_exact_readonly_route_allowlist():
    config=test_runtime.caddyfile("100.64.43.123",21831)
    assert "http://100.64.43.123:21831" in config
    assert "reverse_proxy http://127.0.0.1:8000" in config
    assert "handle /health" in config
    assert "handle /command-center" in config
    assert "handle /control-room" in config
    assert 'respond "Denied" 404' in config
    assert "admin off" in config and "auto_https off" in config
    for header in test_runtime.CANARY_HEADERS:
        assert config.count("header_up -"+header)==3
    assert "0.0.0.0:21831" not in config
    assert "http://100.64.43.123:8000 {" not in config


@pytest.mark.parametrize("bad_ip,port",[
    ("0.0.0.0",21831),("127.0.0.1",21831),
    ("192.168.1.1",21831),("::1",21831),
    ("100.64.43.123",8000),("100.64.43.123",1),
])
def test_bad_binding_or_real_production_port_denied(bad_ip,port):
    with pytest.raises((test_runtime.QualificationDenied,ValueError)):
        test_runtime.caddyfile(bad_ip,port)


def test_lenovo_remote_script_is_bounded_to_get_and_status_only():
    code=test_runtime.REMOTE_SCRIPT
    assert '"/health"' in code
    assert '"/control-room"' in code
    assert '"/openapi.json"' in code
    assert '"synthetic-forgery-not-a-real-user"' in code
    assert 'method="GET"' in code
    assert 'ProxyHandler({})' in code
    assert "results[name]=response.status" in code
    assert "results[name]=exc.code" in code
    for forbidden in (
        'method="POST"','Authorization', 'read(', 'sshpass',
        'docker', 'systemctl', 'OPENAI_API_KEY',
    ):
        assert forbidden not in code


def test_pinned_ssh_and_runtime_without_service_mutations():
    source=Path(test_runtime.__file__).read_text()
    assert '"BatchMode=yes"' in source
    assert '"StrictHostKeyChecking=yes"' in source
    assert '"ConnectTimeout=4"' in source
    assert '"lenovo",remote' in source
    assert '"assistx-api",API_IMAGE,10' in source
    assert '"assistx-hermes-adapter",HERMES_IMAGE,16' in source
    assert 'control-room-ts-proxy.service' in source
    assert 'control-room-ts-proxy.service"],5)' in source
    assert 'with tempfile.TemporaryDirectory(' in source
    assert 'proc.terminate()' in source
    assert 'proc.kill()' in source
    assert '"production_task_mutations":0' in source
    assert '"real_port_8000_cutover_authorized":False' in source
    for mutation in (
        'systemctl stop', 'systemctl restart', 'docker restart',
        'docker stop', 'docker exec', 'tailscale serve --bg',
    ):
        assert mutation not in source


def test_protected_route_redirect_is_explicitly_accounted_for():
    config=test_runtime.caddyfile("100.64.43.123",20001)
    assert 'handle /command-center' in config
    assert 'handle /control-room' in config
    assert '"/control-room",True' in test_runtime.REMOTE_SCRIPT
