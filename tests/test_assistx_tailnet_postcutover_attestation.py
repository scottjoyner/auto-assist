"""Static attestation remains fail-closed before and after cutover."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/assistx_tailnet_postcutover_attestation.py"
SPEC=importlib.util.spec_from_file_location("assistx_postcutover_attestation_test",SCRIPT)
assert SPEC is not None and SPEC.loader is not None
mod=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

CANDIDATE=dict(
    legacy_active=False,legacy_boot_enabled=False,
    caddy_active=True,caddy_boot_enabled=True,
    caddy_process_pinned=True,caddy_config_pinned=True,
    caddy_binary_pinned=True,tailnet_listener=True,
    localhost_listener=True,unexpected_listener=False,
    mobile_serve_scope_exact=True,
)

def evaluate(**kwargs):
    return mod.assess(**{**CANDIDATE,**kwargs})


def test_default_inert():
    output=subprocess.run([sys.executable,str(SCRIPT)],check=True,
                          capture_output=True,text=True,timeout=8)
    report=json.loads(output.stdout)
    assert report["decision"]=="HOLD"
    assert report["physical_executed"] is False
    assert report["production_mutations"]==0


def test_even_static_perfect_and_offhost_auth_never_production_go():
    for client in (False,True):
        report=evaluate(real_offhost_auth_verified=client)
        assert report["postcutover_static_gate_met"] is True
        assert report["decision"]=="HOLD"
        assert report["safe_fallback_activated_and_qualified"] is False
        assert report["production_release_authorized"] is False


@pytest.mark.parametrize("fault",[
    {"legacy_active":True},{"legacy_boot_enabled":True},
    {"caddy_active":False},{"caddy_boot_enabled":False},
    {"caddy_process_pinned":False},{"caddy_config_pinned":False},
    {"caddy_binary_pinned":False},{"tailnet_listener":False},
    {"localhost_listener":False},{"unexpected_listener":True},
    {"mobile_serve_scope_exact":False},
])
def test_every_static_regression_fails_closed(fault):
    report=evaluate(**fault)
    assert report["postcutover_static_gate_met"] is False
    assert report["decision"]=="HOLD"
    assert report["production_mutations"]==0


def test_nonboolean_inputs_fail_closed():
    for field in CANDIDATE:
        with pytest.raises(mod.Unsafe):
            evaluate(**{field:"false"})


def test_missing_binary_or_config_fails_pin(tmp_path):
    assert mod.is_pinned_file(tmp_path/"absent",mod.EXPECTED_CONFIG_SHA) is False
    target=tmp_path/"config"
    target.write_text("test")
    assert mod.is_pinned_file(target,mod.EXPECTED_CONFIG_SHA) is False
    link=tmp_path/"symlink"
    link.symlink_to(target)
    assert mod.is_pinned_file(link,mod.EXPECTED_CONFIG_SHA) is False


def test_process_rejects_invalid_identity():
    for pid in (-1,0,4194305):
        assert mod.process_identity(pid) is False


def test_no_mutating_commands_or_raw_socat_recovery():
    content=SCRIPT.read_text()
    assert '"systemctl","--user","is-active"' in content
    assert '"systemctl","--user","is-enabled"' in content
    assert "str(EXPECTED_CONFIG)" in content
    assert 'sha256' in content
    assert "safe_fallback_activated_and_qualified" in content
    for forbidden in ("docker restart","systemctl stop","systemctl disable",
                      "systemctl restart","tailscale serve --bg",
                      "socat TCP-LISTEN","method=\"POST\"","get_basic_in_memory"):
        assert forbidden not in content
