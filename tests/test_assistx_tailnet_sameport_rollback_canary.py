"""Unit/static checks: disposable same-port sanitizer rollback rehearsal."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

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
    assert 'staged_caddyfile(backend_port,port)' in src
    assert 'b"synthetic-user:synthetic-pass"' in src


def test_exact_reviewed_release_config_is_used_for_disposable_rehearsal():
    raw=canary.RELEASE_CADDYFILE.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==canary.REVIEWED_CADDYFILE_SHA256
    candidate=canary.staged_caddyfile(18317,18318)
    assert "http://127.0.0.1:18318 {" in candidate
    assert "reverse_proxy http://127.0.0.1:18317 {" in candidate
    assert "http://{$ASSISTX_TAILNET_IP}:8000" not in candidate
    assert "http://127.0.0.1:8000" not in candidate
    assert candidate.count("reverse_proxy")==1
    for identity in canary.DROPPED_HEADERS:
        assert candidate.count("header_up -"+identity)==1


def test_fail_closed_if_release_file_changed(monkeypatch,tmp_path):
    fake=tmp_path/"release.Caddyfile"
    fake.write_text(canary.RELEASE_CADDYFILE.read_text()+"\n# unreviewed\n")
    monkeypatch.setattr(canary,"RELEASE_CADDYFILE",fake)
    try:
        canary.staged_caddyfile(18317,18318)
    except canary.UnsafeRehearsal as exc:
        assert "changed without renewed review" in str(exc)
    else:
        raise AssertionError("altered release config erroneously admitted")


def test_staged_config_rejects_live_ports_and_collisions():
    import pytest
    for upstream,frontend in ((8000,18318),(18317,8000),
                              (18317,18317),(1,18318),(18317,65536)):
        with pytest.raises(canary.UnsafeRehearsal):
            canary.staged_caddyfile(upstream,frontend)


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



def test_actual_opencode_dispatch_source_is_compiled_without_importing_workflows():
    source=Path(canary.__file__).read_text()
    assert "scripts/opencode-bridge/dispatch_assistx_task.py" in source
    assert 'item.name=="_post"' in source
    assert 'ast.Module(body=[fn],type_ignores=[])' in source
    assert 'f"http://127.0.0.1:{port}/synthetic-opencode-dispatch"' in source
    assert '"synthetic-opencode","synthetic-pass"' in source
    assert 'opener.open(request,timeout=3)' in source
    assert 'checks["real_opencode_bridge_dispatch_contract_sanitized"]' in source
    assert 'result.get("forwarded_identity") is False' in source

def test_real_opencode_config_sync_post_compiled_without_running_fleet_scans():
    source=Path(canary.__file__).read_text()
    assert "scripts/opencode-bridge/sync_configs_to_assistx.py" in source
    assert 'item.name=="_post"' in source
    assert 'f"http://127.0.0.1:{port}{route}"' in source
    assert '"synthetic-opencode","synthetic-pass"' in source
    assert '"synthetic-config-sync-contract"' in source
    assert '"/api/swarm/model-endpoints/register"' in source
    assert '"/api/swarm/model-endpoints/synthetic/probe"' in source
    assert 'checks["real_opencode_config_sync_register_and_probe_sanitized"]' in source
    assert 'and v.get("forwarded_identity") is False' in source
