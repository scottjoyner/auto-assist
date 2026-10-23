"""Unit/static checks: disposable same-port sanitizer rollback rehearsal."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import assistx_tailnet_sameport_rollback_canary as canary


def test_default_never_touches_production():
    proc=subprocess.run([sys.executable,str(Path(canary.__file__))],
                        capture_output=True,text=True,timeout=8,check=True)
    state=json.loads(proc.stdout)
    assert state["physical_executed"] is False
    assert state["real_tailnet_or_production_changes"]==0
    assert state["operator_cutover_authorized"] is False


def test_dedicated_loopback_ports_and_synthetic_backend():
    src=Path(canary.__file__).read_text()
    assert 'ThreadingHTTPServer(("127.0.0.1",0),Echo)' in src
    assert 'port=free_port()' in src
    assert 'f"TCP-LISTEN:{port},bind=127.0.0.1,fork,reuseaddr"' in src
    assert 'f"TCP:127.0.0.1:{backend_port}"' in src
    assert 'caddyfile(backend_port,port)' in src
    assert 'b"synthetic-user:synthetic-pass"' in src


def test_real_rollback_is_to_sanitizer_not_raw_forwarder():
    src=Path(canary.__file__).read_text()
    assert 'checks["synthetic_raw_socat_forwards_unsafe_header"]=True' in src
    assert 'checks["same_port_caddy_preserves_basic_but_strips_identity"]=True' in src
    assert 'checks["sanitizing_rollback_remains_fail_closed"]=True' in src
    assert 'wait_ready(current,port,expect_identity=True)' in src
    assert src.count('wait_ready(current,port,expect_identity=False)')==2
    assert '"old_socat_rollback_security_safe":False' in src
    assert '"operator_cutover_authorized":False' in src


def test_no_production_subprocess_or_secrets():
    src=Path(canary.__file__).read_text()
    for forbidden in (
        "docker inspect","docker exec","docker restart","docker stop",
        "systemctl stop","systemctl restart","tailscale serve",
        "100.64.","--network host","OPENAI_API_KEY","TRUSTED_AUTH_HEADER=",
    ):
        assert forbidden not in src
    assert '"real_credentials_or_tasks_accessed":0' in src
    assert '"real_tailnet_or_production_changes":0' in src
    assert 'with tempfile.TemporaryDirectory(' in src


def test_all_disposable_processes_cleaned():
    src=Path(canary.__file__).read_text()
    assert 'stop(current)' in src
    assert 'backend.shutdown()' in src
    assert 'backend.server_close()' in src
    assert 'task.join(timeout=3)' in src
