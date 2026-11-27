"""Fail-closed tests for transient user-systemd Caddy Basic-auth rehearsal."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import assistx_tailnet_systemd_auth_canary as canary
import pytest


def test_default_inert():
    done=subprocess.run([sys.executable,str(Path(canary.__file__))],
                        capture_output=True,text=True,check=True,timeout=8)
    result=json.loads(done.stdout)
    assert result["decision"]=="HOLD"
    assert result["physical_executed"] is False
    assert result["production_cutover_authorized"] is False


def test_derived_release_config_is_loopback_only():
    config=canary.isolated_config(22661)
    assert config.count("http://127.0.0.1:22661 {")==1
    assert config.count("reverse_proxy http://127.0.0.1:8000 {")==1
    assert config.count("reverse_proxy")==1
    assert "{$ASSISTX_TAILNET_IP}" not in config
    assert "admin off" in config and "auto_https off" in config
    from assistx_tailnet_sanitizer_canary import DROPPED_HEADERS
    for header in DROPPED_HEADERS:
        assert config.count("header_up -"+header)==1


@pytest.mark.parametrize("port",[0,1025,8000,19001,65536])
def test_bad_ports_fail_closed(port):
    with pytest.raises(canary.Denied):
        canary.isolated_config(port)


def test_unreviewed_config_change_fails_closed(monkeypatch,tmp_path):
    from assistx_tailnet_sameport_rollback_canary import (
        staged_caddyfile,
    )
    source=staged_caddyfile.__globals__
    changed=tmp_path/"modified.Caddyfile"
    changed.write_bytes(source["RELEASE_CADDYFILE"].read_bytes()+b"# changed")
    monkeypatch.setitem(source,"RELEASE_CADDYFILE",changed)
    with pytest.raises(ValueError,match="changed without renewed review"):
        canary.isolated_config(22661)


def test_never_stops_an_unrecognized_service(monkeypatch):
    monkeypatch.setattr(canary,"command",
                        lambda *a,**k: pytest.fail("should not call systemctl"))
    for unit in ("control-room-ts-proxy.service",
                 "assistx-sanitizer-proof-user.service",
                 "assistx-sanitizer-proof-zzzzzzzzzzzz.service"):
        with pytest.raises(canary.Denied):
            canary.stop_unit(unit)


def test_no_production_mutations_and_bounded_actual_requests():
    source=Path(canary.__file__).read_text()
    assert 'for stage in ("primary","fallback")' in source
    assert '"--property=RuntimeMaxSec=45"' in source
    assert '"--property=Restart=no"' in source
    assert '"--property=NoNewPrivileges=yes"' in source
    assert 'stop_unit(name)' in source
    assert 'exact_live_state()' in source
    assert 'get_basic_in_memory(api)' in source
    assert 'status(port,{' in source
    assert '"Tailscale-User-Login":"synthetic-forged-identity"' in source
    assert '"production_task_writes":0' in source
    assert '"production_cutover_authorized":False' in source
    for disallowed in ('docker restart','docker stop','docker exec',
                       'tailscale serve','control-room-ts-proxy.service',
                       'method="POST"','systemctl --user restart'):
        assert disallowed not in source
