"""Non-mutating unit contract for authorized Basic compatibility rehearsal."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
import assistx_tailnet_real_basic_canary as canary


def test_default_inert_and_never_reads_docker_secrets():
    result=subprocess.run([sys.executable,str(Path(canary.__file__))],
                          capture_output=True,text=True,timeout=7,check=True)
    state=json.loads(result.stdout)
    assert state["physical_executed"] is False
    assert state["production_task_mutations"]==0
    assert state["production_cutover_authorized"] is False


def test_auth_helper_requires_real_pinned_runtime_and_configured_header():
    metadata={
        "Config":{"Env":[
            "TRUSTED_AUTH_HEADER=Tailscale-User-Login",
            "BASIC_AUTH_USER=synthetic-user",
            "BASIC_AUTH_PASS=synthetic-password",
        ]},
    }
    encoded=canary.get_basic_in_memory(metadata)
    assert encoded.startswith("Basic ")
    # Never expose/compare actual production values.
    assert "synthetic-password" not in encoded
    with pytest.raises(canary.Denied):
        canary.get_basic_in_memory({
            "Config":{"Env":[
                "TRUSTED_AUTH_HEADER=Other-Header",
                "BASIC_AUTH_USER=synthetic-user",
                "BASIC_AUTH_PASS=synthetic-password",
            ]}})
    with pytest.raises(canary.Denied):
        canary.get_basic_in_memory({
            "Config":{"Env":[
                "TRUSTED_AUTH_HEADER=Tailscale-User-Login",
                "BASIC_AUTH_USER=synthetic-user",
            ]}})


def test_duplicate_env_keys_are_denied():
    with pytest.raises(canary.Denied):
        canary.get_basic_in_memory({
            "Config":{"Env":[
                "TRUSTED_AUTH_HEADER=Tailscale-User-Login",
                "BASIC_AUTH_USER=first",
                "BASIC_AUTH_USER=second",
                "BASIC_AUTH_PASS=irrelevant",
            ]}})


def test_real_authenticated_canary_is_loopback_only_and_get_only():
    source=Path(canary.__file__).read_text()
    assert 'url=f"http://127.0.0.1:{port}/command-center"' in source
    assert 'method="GET"' in source
    assert 'caddyfile(8000,port)' in source
    assert 'port=free_port()' in source
    assert '"ASSISTX_REJECT_LEGACY_TRUSTED_HEADER"' not in source
    assert 'Tailscale-User-Login":"synthetic-forged-identity"' in source
    assert 'auth_only==200' in source
    assert 'mixed==200' in source
    assert 'spoof_only==401' in source
    assert 'resp.read(' not in source
    assert 'error.read(' not in source
    assert 'print(authorization)' not in source
    assert 'print(values)' not in source


def test_production_originals_and_disposable_caddy_cleanup_enforced():
    source=Path(canary.__file__).read_text()
    assert 'get_basic_in_memory(api)' in source
    assert 'exact_live_state()' in source
    assert 'after_proxy==proxy' in source
    assert 'process.terminate()' in source
    assert 'process.kill()' in source
    assert 'with tempfile.TemporaryDirectory(' in source
    assert '"production_cutover_authorized":False' in source
    for forbidden in (
        "systemctl restart","systemctl stop","tailscale serve",
        "docker restart","docker stop","docker exec",
        '"method":"POST"', '"--network","host"', "100.64.43.123:8000",
    ):
        assert forbidden not in source
