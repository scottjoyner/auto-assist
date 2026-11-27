"""Rootless startup must never import inherited config or follow scratch links.

Only disposable local pytest tmp_path files and a harmless /bin/true command
are used. This does not start any Docker/production Hermes/provider process.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ENTRY = Path(__file__).resolve().parent / 'hermes_split_root_candidate_entrypoint.sh'


def run_entry(tmp_path, *, config=True, home=None, hermes_home=None,
              config_dir=None, variables=None):
    workspace = home if home is not None else tmp_path / 'workspace'
    source_home = hermes_home if hermes_home is not None else workspace / 'hermes-home'
    target_dir = config_dir if config_dir is not None else workspace / '.hermes'
    if config:
        source_home.mkdir(parents=True, exist_ok=True)
        (source_home / 'config.yaml').write_text('synthetic: true\n')
    env = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': str(workspace),
        'HERMES_HOME': str(source_home),
        'HERMES_CONFIG_HOME': str(target_dir),
    }
    env.update(variables or {})
    return subprocess.run(['bash', str(ENTRY), '/bin/true'],
                          env=env, capture_output=True, text=True,
                          check=False, timeout=6)


@pytest.mark.skipif(os.geteuid() == 0, reason='test requires unprivileged runner')
def test_preprovisioned_workspace_config_allows_only_local_noop(tmp_path):
    proc = run_entry(tmp_path)
    assert proc.returncode == 0, proc.stderr
    workspace = tmp_path / 'workspace'
    config = workspace / 'hermes-home' / 'config.yaml'
    alias = workspace / '.hermes' / 'config.yaml'
    assert config.read_text() == 'synthetic: true\n'
    assert alias.is_symlink() and os.readlink(alias) == str(config)
    assert sorted(p.name for p in (workspace / 'hermes-home').iterdir()) == [
        'cache','config.yaml','harvest','logs','memories','profiles',
        'sessions','tmp',
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason='test requires unprivileged runner')
def test_missing_approved_config_fails_before_workspace_writes(tmp_path):
    proc = run_entry(tmp_path, config=False)
    assert proc.returncode == 64
    assert 'independently preprovisioned Hermes config required' in proc.stderr
    assert not (tmp_path / 'workspace').exists()


@pytest.mark.skipif(os.geteuid() == 0, reason='test requires unprivileged runner')
def test_source_config_symlink_to_other_workspace_denied(tmp_path):
    workspace = tmp_path / 'workspace'
    (workspace / 'hermes-home').mkdir(parents=True)
    outside = tmp_path / 'other-config.yaml'
    outside.write_text('unsafe: true\n')
    (workspace / 'hermes-home' / 'config.yaml').symlink_to(outside)
    proc = run_entry(tmp_path, config=False)
    assert proc.returncode == 64
    assert 'preprovisioned' in proc.stderr
    assert not (workspace / '.hermes').exists()
    assert outside.read_text() == 'unsafe: true\n'


@pytest.mark.skipif(os.geteuid() == 0, reason='test requires unprivileged runner')
def test_preexisting_different_alias_rejected_without_overwrite(tmp_path):
    workspace = tmp_path / 'workspace'
    (workspace / '.hermes').mkdir(parents=True)
    bad = workspace / '.hermes' / 'config.yaml'
    bad.write_text('untrusted: true\n')
    proc = run_entry(tmp_path)
    assert proc.returncode == 64
    assert 'unapproved preexisting Hermes config alias' in proc.stderr
    assert bad.read_text() == 'untrusted: true\n'


@pytest.mark.skipif(os.geteuid() == 0, reason='test requires unprivileged runner')
def test_preexisting_matching_alias_idempotent(tmp_path):
    workspace = tmp_path / 'workspace'
    source = workspace / 'hermes-home'
    (workspace / '.hermes').mkdir(parents=True)
    source.mkdir()
    (source / 'config.yaml').write_text('synthetic: true\n')
    (workspace / '.hermes' / 'config.yaml').symlink_to(source / 'config.yaml')
    proc = run_entry(tmp_path)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason='test requires unprivileged runner')
@pytest.mark.parametrize('bad_path', [
    '/root/.hermes', '/etc/hermes', '/proc/test',
    '/tmp/../workspace', '/tmp//workspace', '/tmp/./workspace',
    '/tmp/work\nspace', '/tmp/work\tspace', '/tmp/work/',
])
def test_unsafe_home_and_traversal_denied(tmp_path, bad_path):
    proc = run_entry(tmp_path, config=False, home=Path(bad_path))
    assert proc.returncode != 0


@pytest.mark.skipif(os.geteuid() == 0, reason='test requires unprivileged runner')
def test_symlinked_workspace_ancestor_denied(tmp_path):
    actual = tmp_path / 'actual'
    actual.mkdir()
    link = tmp_path / 'linked'
    link.symlink_to(actual, target_is_directory=True)
    proc = run_entry(tmp_path, config=False, home=link)
    assert proc.returncode == 64
    assert 'symlinked workspace path denied' in proc.stderr
    assert not (actual / 'hermes-home').exists()


@pytest.mark.skipif(os.geteuid() == 0, reason='test requires unprivileged runner')
def test_redirected_session_directory_denied_before_alias_written(tmp_path):
    workspace = tmp_path / 'workspace'
    source = workspace / 'hermes-home'
    source.mkdir(parents=True)
    (source / 'config.yaml').write_text('synthetic: true\n')
    outside = tmp_path / 'unrelated'
    outside.mkdir()
    (source / 'sessions').symlink_to(outside, target_is_directory=True)
    proc = run_entry(tmp_path, config=False)
    assert proc.returncode == 64
    assert 'symlinked workspace path denied' in proc.stderr
    assert not (workspace / '.hermes').exists()


@pytest.mark.skipif(os.geteuid() == 0, reason='test requires unprivileged runner')
def test_environment_credential_seed_denied_before_writes(tmp_path):
    proc = run_entry(tmp_path, config=False, variables={'ENV_SEED': ''})
    assert proc.returncode == 64
    assert 'refuses credential environment seeding' in proc.stderr
    assert not (tmp_path / 'workspace').exists()


def test_script_has_no_unreviewed_inherited_config_import():
    script = ENTRY.read_text()
    assert 'cp /app/hermes-home.defaults/config.yaml' not in script
    assert 'independently preprovisioned Hermes config required' in script
    assert 'symlinked workspace path denied' in script
    assert 'root execution denied in hardened Hermes candidate' in script
    assert 'non-root startup refuses credential environment seeding' in script
    assert 'exec "$@"' in script


def test_previous_physical_canary_is_suspended_before_docker_calls():
    import hermes_split_root_normal_compose_canary as canary

    assert canary.PHYSICAL_REQUALIFICATION_READY is False
    with pytest.raises(canary.UnsafeCanary, match='earlier physical Docker canary'):
        canary.run()
