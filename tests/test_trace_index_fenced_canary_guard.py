"""Safety controls only; general CI must never launch this Docker test."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / 'scripts/trace_index_fenced_redis_canary.py'
    spec = importlib.util.spec_from_file_location('trace_fenced_disposable_canary', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unapproved_cannot_start_docker(monkeypatch):
    obj = module()
    monkeypatch.setattr(obj.subprocess, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('docker must not run without preflight')
    ))
    assert obj.main([]) == 2
    assert obj.main(['--expected-image-id', 'sha256:'+'a'*64]) == 2


def test_invalid_image_ref_never_accepted_for_execution():
    obj = module()
    for invalid in ('redis:7-alpine', 'latest', 'sha256:short'):
        with pytest.raises(ValueError):
            obj.command(invalid, 'assistx-fence-study-abcdef012345')


def test_guarded_docker_has_no_network_ports_or_host_mounts():
    obj = module()
    digest = 'sha256:'+'a'*64
    cmd = obj.command(digest, 'assistx-fence-study-abcdef012345')
    assert cmd[-2] == digest
    assert cmd[cmd.index('--pull') + 1] == 'never'
    assert cmd[cmd.index('--network') + 1] == 'none'
    assert cmd[cmd.index('--memory') + 1] == '96m'
    assert cmd[cmd.index('--cpus') + 1] == '0.25'
    assert '--read-only' in cmd and '--rm' in cmd
    assert '--cap-drop' in cmd and 'ALL' in cmd
    assert 'no-new-privileges' in cmd
    for forbidden in ('-p', '--publish', '-v', '--volume', '--privileged', '--network=host'):
        assert forbidden not in cmd


def test_canary_is_exactly_three_source_owned_lua_scripts():
    obj = module()
    scripts = obj.scripts_from_source()
    assert set(scripts) == {'ACQUIRE_LUA', 'RENEW_LUA', 'RELEASE_LUA'}
    assert all("redis.call('TIME')" in script for script in scripts.values())
    shell = obj.shell_code(scripts)
    assert 'UNIFIED_FENCED_REDIS_CANARY_PASS' in shell
    assert 'synthetic' not in str(obj.command('sha256:'+'a'*64,
                                           'assistx-fence-study-abcdef012345'))
    assert 'http://' not in shell and 'curl ' not in shell
