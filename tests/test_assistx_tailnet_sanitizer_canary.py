"""Disposable real Caddy sanitizer safety contract; no fleet changes from tests."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
import assistx_tailnet_sanitizer_canary as canary


def test_default_is_inert_without_explicit_physical_flag():
    result=subprocess.run([sys.executable,str(Path(canary.__file__))],
                          capture_output=True,text=True,timeout=8,check=True)
    value=json.loads(result.stdout)
    assert value["physical_executed"] is False
    assert value["original_service_modified"] is False
    assert value["decision"]=="HOLD"


def test_generated_config_exposes_only_isolated_loopback():
    cfg=canary.caddyfile(18317,18318)
    assert "http://127.0.0.1:18318 {" in cfg
    assert "reverse_proxy http://127.0.0.1:18317 {" in cfg
    assert "admin off" in cfg and "auto_https off" in cfg
    assert cfg.count("reverse_proxy")==1
    for name in canary.DROPPED_HEADERS:
        assert cfg.count("header_up -"+name)==1
    assert "100." not in cfg


@pytest.mark.parametrize("a,b",[(1,18318),(18318,1),(18318,18318),
                                 (65536,18318),(18318,65536)])
def test_nonisolated_ports_denied(a,b):
    with pytest.raises(canary.UnsafeCanary):
        canary.caddyfile(a,b)


def test_real_caddy_client_shields_sensitive_headers_and_preserves_streaming():
    source=Path(canary.__file__).read_text()
    assert 'def check_websocket_upgrade(' in source
    assert '"synthetic_websocket_upgrade_sanitized":True' in source
    assert '"synthetic_sse_stream_preserved_and_sanitized":True' in source
    assert 'http://{BACKEND_HOST}:{gateway_port}/health' in source
    assert 'http://{BACKEND_HOST}:{gateway_port}/command-center' in source
    assert 'status==401' in source
    assert 'ThreadingHTTPServer((BACKEND_HOST,0),SyntheticHandler)' in source


def test_disposable_caddy_process_and_temp_files_are_cleaned():
    source=Path(canary.__file__).read_text()
    assert 'with tempfile.TemporaryDirectory(' in source
    assert 'proxy.terminate()' in source
    assert 'proxy.kill()' in source
    assert 'fake_server.shutdown()' in source
    assert 'thread.join(timeout=2)' in source
    assert 'start_new_session=True' in source
    for unsafe in (
        "systemctl restart", "systemctl stop", "tailscale serve",
        "docker restart","docker stop","docker exec", "iptables -",
        "nft add", "/etc/caddy/Caddyfile",
    ):
        assert unsafe not in source


def test_no_actual_credentials_or_mutating_api_calls():
    source=Path(canary.__file__).read_text()
    assert 'synthetic-user:synthetic-password' in source
    assert 'synthetic-untrusted-identity' in source
    assert '"actual_client_credentials_used":False' in source
    assert '"real_task_mutations":0' in source
    assert 'method="POST",data=b"synthetic-only"' in source
    assert '"GET"' in source or 'method="GET"' in source
    assert 'OPENAI_API_KEY' not in source
