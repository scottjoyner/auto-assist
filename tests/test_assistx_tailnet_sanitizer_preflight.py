"""Standalone, readonly Caddy-to-AssistX ingress preflight unit checks."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Hosted CI has tests/, but not scripts/, on its Python import path.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import assistx_tailnet_sanitizer_canary as canary
import assistx_tailnet_sanitizer_preflight as preflight  # noqa: E402


def actual_config():
    return preflight.CADDYFILE.read_text()


def test_config_matches_all_real_caddy_sanitizer_headers():
    assert preflight.DROPPED_HEADERS==canary.DROPPED_HEADERS
    config=actual_config()
    result=preflight.inspect_contract(
        config,socat_active=True,
        header_configured=True,header_expected=True,basic_available=True)
    assert result["sanitizer_config_exact_rules_valid"]
    assert result["automatic_cutover_authorized"] is False
    assert result["production_mutations"]==0
    assert result["release_rollback_verified"] is False


@pytest.mark.parametrize("change",[
    lambda s:s.replace("header_up -Tailscale-User-Login",""),
    lambda s:s.replace("header_up -X-Forwarded-User",""),
    lambda s:s.replace("header_up -Tailscale-User-Login",
                        "header_up -Tailscale-User-Login\n        header_up -Tailscale-User-Login"),
    lambda s:s.replace("reverse_proxy http://127.0.0.1:8000 {",
                        "reverse_proxy http://0.0.0.0:8000 {"),
    lambda s:s.replace("http://{$ASSISTX_TAILNET_IP}:8000",
                        "http://0.0.0.0:8000"),
    lambda s:s.replace("admin off","admin on"),
    lambda s:s.replace("    bind 100.64.43.123",""),
    lambda s:s.replace("    bind 100.64.43.123","    bind 0.0.0.0"),
])
def test_corrupt_or_unsafe_config_denied(change):
    with pytest.raises(preflight.Blocked):
        preflight.inspect_contract(change(actual_config()),
            socat_active=True,header_configured=True,
            header_expected=True,basic_available=True)


@pytest.mark.parametrize("flags",[
    {"socat_active":False,"header_configured":True,
     "header_expected":True,"basic_available":True},
    {"socat_active":True,"header_configured":False,
     "header_expected":True,"basic_available":True},
    {"socat_active":True,"header_configured":True,
     "header_expected":False,"basic_available":True},
    {"socat_active":True,"header_configured":True,
     "header_expected":True,"basic_available":False},
])
def test_missing_production_preconditions_denied(flags):
    with pytest.raises(preflight.Blocked):
        preflight.inspect_contract(actual_config(),**flags)


def test_caddy_adapt_never_wildcard_even_if_tailnet_env_is_unset():
    """Extra physical assurance on x1-370; static guard runs everywhere."""
    config=actual_config()
    assert config.count("    bind 100.64.43.123")==1
    assert "    bind 0.0.0.0" not in config
    assert "    bind {$ASSISTX_TAILNET_IP}" not in config
    assert "Caddy listener" in Path(preflight.__file__).read_text()
    assert "caddy_explicit_tailnet_only_listener_with_env_set_and_unset" in Path(preflight.__file__).read_text()
    import os
    if not Path("/usr/bin/caddy").is_file():
        pytest.skip("physical Caddy binary unavailable on this CI host")
    for value in ("100.64.43.123",None):
        env={k:v for k,v in os.environ.items() if k!="ASSISTX_TAILNET_IP"}
        if value is not None:
            env["ASSISTX_TAILNET_IP"]=value
        adapted=subprocess.run(["/usr/bin/caddy","adapt","--config",
                                str(preflight.CADDYFILE),"--adapter","caddyfile"],
                               capture_output=True,text=True,env=env,timeout=10,check=True)
        j=json.loads(adapted.stdout)
        listeners=[server.get("listen") for server in j.get("apps",{}).get("http",{}).get("servers",{}).values()]
        assert listeners==[["100.64.43.123:8000"]]


def test_default_cli_is_inert_and_standalone():
    p=subprocess.run(
        [sys.executable,str(Path(preflight.__file__))],
        text=True,capture_output=True,timeout=8,check=True)
    result=json.loads(p.stdout)
    assert result["physical_executed"] is False
    assert result["decision"]=="HOLD"
    assert result["production_mutations"]==0
    assert result["automatic_cutover_authorized"] is False


def test_preflight_checks_real_service_without_mutating_system():
    source=Path(preflight.__file__).read_text()
    assert 'control-room-ts-proxy.service' in source
    assert 'TCP:127.0.0.1:8000' in source
    assert '"caddy","validate"' in source or '"/usr/bin/caddy","validate"' in source
    assert '"tailscale","ip","-4"' in source
    assert 'TRUSTED_AUTH_HEADER' in source
    assert 'BASIC_AUTH_USER' in source
    assert 'BASIC_AUTH_PASS' in source
    for command in ("systemctl stop","systemctl restart",
                    "docker restart","docker stop","tailscale serve --bg",
                    "nft add","ufw disable"):
        assert command not in source
