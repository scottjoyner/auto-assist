"""Offline checks for the disposable systemd failure-and-safe-rollback test."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
import assistx_tailnet_sanitizer_fault_rollback_canary as canary


def test_default_inert_without_explicit_opt_in():
    result=subprocess.run([sys.executable,str(Path(canary.__file__))],
                          capture_output=True,text=True,check=True,timeout=7)
    decoded=json.loads(result.stdout)
    assert decoded["decision"]=="HOLD"
    assert decoded["physical_executed"] is False
    assert decoded["real_production_cutover_verified"] is False


@pytest.mark.parametrize("name",[
    "", "control-room-ts-proxy.service", "assistx-api.service",
    "assistx-sanitizer-proof-abc.service",
    "assistx-sanitizer-proof-000000000000.service;rm -rf /",
    "assistx-sanitizer-proof-ABCDEF000000.service",
])
def test_only_generated_disposable_unit_names_allowed(name):
    assert canary.safe_name(name) is False


def test_disposable_name_pattern_allows_exact_helper():
    from assistx_tailnet_systemd_auth_canary import unit_name
    assert canary.safe_name(unit_name()) is True


def test_default_operational_commands_are_bounded_and_fail_closed():
    source=Path(canary.__file__).read_text()
    assert "--physical-disposable-user-systemd" in source
    assert "get_basic_in_memory(old_api)" in source
    assert "exact_live_state()" in source
    assert "isolated_config(port)" in source
    assert 'not in (8000,19001)' in source
    assert '"/usr/bin/caddy","validate"' in source
    assert 'validate_bad.returncode!=0' in source
    assert '"/usr/bin/caddy","run"' in source
    assert 'Restart=no' in source
    assert 'NoNewPrivileges=yes' in source
    assert 'RuntimeMaxSec=45' in source
    assert 'RuntimeMaxSec=12' in source


def test_fault_injected_between_primary_and_sanitized_fallback():
    source=Path(canary.__file__).read_text()
    parts=[
        'checks["initial_sanitized_basic_and_forgery_checks"]=True',
        'stop_unit(primary)',
        'checks["invalid_transient_rejected_no_listener"]=await_failed(faulty,port)',
        'start_disposable(backup,good)',
        'checks["sanitized_fallback_same_port_authenticated"]=True',
        'stop_unit(backup)',
        'checks["sanitized_fallback_port_released"]=True',
    ]
    positions=[source.index(fragment) for fragment in parts]
    assert positions==sorted(positions)
    assert '"rollback_to_unsafe_socat_performed":False' in source
    assert '"real_production_cutover_verified":False' in source
    assert '"production_task_writes":0' in source


def test_invalid_service_does_not_get_automatic_restart():
    source=Path(canary.__file__).read_text()
    assert '"--property=Restart=no"' in source
    assert 'await_failed(faulty,port)' in source
    assert 'stop_unit(backup)' in source
    assert 'stop_unit(primary)' in source
    assert 'safe_name(name)' in source


def test_no_production_service_or_real_tasks_touched():
    source=Path(canary.__file__).read_text()
    for forbidden in (
        'systemctl","--user","stop","control-room-ts-proxy.service"',
        'systemctl restart', 'docker restart', 'docker stop',
        'docker exec', 'tailscale serve --bg',
        'HTTPBasicAuth(', '/api/tasks',
        'print(basic)', 'print(password)', 'sshpass',
    ):
        assert forbidden not in source
    assert 'TemporaryDirectory(prefix="assistx-sanitizer-fault-")' in source
    assert '"real_basic_credential_values_logged":False' in source
