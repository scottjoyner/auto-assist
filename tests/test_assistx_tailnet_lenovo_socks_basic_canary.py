"""Static, pure, fail-closed tests for the off-host Basic HTTP canary."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import assistx_tailnet_lenovo_socks_basic_canary as canary
import pytest


def test_inert_default():
    result=subprocess.run([sys.executable,str(Path(canary.__file__))],
                          capture_output=True,text=True,check=True,timeout=8)
    state=json.loads(result.stdout)
    assert state["decision"]=="HOLD"
    assert state["physical_executed"] is False
    assert state["live_cutover_authorized"] is False


def test_pinned_release_derived_and_get_allowlist():
    config=canary.release_config("100.64.43.123",22391)
    assert "http://100.64.43.123:22391 {" in config
    assert "reverse_proxy http://127.0.0.1:8000 {" in config
    assert "method GET" in config
    assert "path /health /control-room" in config
    assert 'respond "Denied" 404' in config
    assert config.count("reverse_proxy")==1
    assert "{$ASSISTX_TAILNET_IP}" not in config
    for name in canary.DROPPED_HEADERS:
        assert config.count("header_up -"+name)==1


@pytest.mark.parametrize("ip,port",[
    ("0.0.0.0",22391),("127.0.0.1",22391),("192.168.0.1",22391),
    ("::1",22391),("100.64.43.123",8000),("100.64.43.123",1),
    ("100.64.43.123",65536),
])
def test_unsafe_listener_refused(ip,port):
    with pytest.raises((canary.Refused,ValueError)):
        canary.release_config(ip,port)


def test_modified_reviewed_source_fails_closed(monkeypatch,tmp_path):
    config=tmp_path/"unreviewed.Caddyfile"
    config.write_bytes(canary.RELEASE_CADDYFILE.read_bytes()+b"\n# changed\n")
    monkeypatch.setattr(canary,"RELEASE_CADDYFILE",config)
    with pytest.raises(canary.Refused,match="unreviewed release bytes"):
        canary.release_config("100.64.43.123",22391)


@pytest.mark.parametrize("ip,port",[
    ("127.0.0.1",22391),("100.64.43.123",8000),
    ("::1",22391),("100.64.43.123",65536),
])
def test_socks_targets_fail_closed_before_dial(ip,port):
    with pytest.raises((canary.Refused,ValueError)):
        canary.socks_connect(23001,ip,port)


def test_only_allowlisted_status_paths():
    with pytest.raises(canary.Refused):
        canary.read_only_status(23001,"100.64.43.123",22391,
                                path="/api/tasks")


def test_exact_remote_ssh_safety_and_cleanup_guards():
    source=Path(canary.__file__).read_text()
    for needle in (
        '"BatchMode=yes"', '"StrictHostKeyChecking=yes"',
        '"ExitOnForwardFailure=yes"', '"127.0.0.1:{proxy_port}"',
        'seen.get("peer")==expected_peer',
        'stop(ssh)', 'stop(caddy)', 'exact_live_state()',
        'get_basic_in_memory(api)', 'basic.startswith("Basic ")',
        '"production_task_writes":0', '"live_cutover_authorized":False',
        '"credentials_written_to_lenovo":False',
        '"protected_response_bodies_read":False',
    ):
        assert needle in source
    for forbidden in (
        'systemctl stop','systemctl restart','docker stop','docker restart',
        'docker exec','tailscale serve','method="POST"','/etc/caddy/Caddyfile',
    ):
        assert forbidden not in source


def test_script_does_not_print_credentials_or_response_bodies():
    source=Path(canary.__file__).read_text()
    assert 'sock.recv(1)' in source
    assert 'return int(words[1])' in source
    assert 'sock.sendall((header+' in source
    assert 'stderr=subprocess.DEVNULL' in source
    assert 'stdout=subprocess.DEVNULL' in source
    assert 'print(json.dumps(report,sort_keys=True))' in source
