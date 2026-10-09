"""Offline guard tests. Docker is NEVER invoked by pytest."""
from __future__ import annotations
import copy
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def subject():
    path = ROOT / 'scripts/run_trace_real_neo4j_cancel_isolated.py'
    spec = importlib.util.spec_from_file_location('isolated_cancel_guard', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def fixture():
    x = subject(); sha = 'sha256:' + 'a' * 64
    network = {'Name': x.NETWORK, 'Internal': True, 'Attachable': False,
               'Ingress': False, 'Containers': {'id': {'Name': x.SERVER}}}
    host = {'Name': '/' + x.SERVER, 'Image': sha, 'State': {'Running': True},
            'HostConfig': {'NetworkMode': x.NETWORK, 'PortBindings': {}, 'Binds': None,
                           'Memory': 2 * 1024**3, 'NanoCpus': 750000000,
                           'Tmpfs': {'/data': 'rw', '/logs': 'rw', '/tmp': 'rw'}},
            'Mounts': [], 'Config': {'Env': ['NEO4J_AUTH=none',
                                              'NEO4J_ACCEPT_LICENSE_AGREEMENT=yes']}}
    return x, network, host, sha

def test_without_approval_never_runs_commands(monkeypatch):
    x = subject()
    monkeypatch.setattr(x.subprocess, 'run', lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError('no process before explicit approval')))
    assert x.main([]) == 2
    assert x.main(['--expected-source-sha', 'a' * 40]) == 2

def test_valid_isolated_topology():
    x, network, host, sha = fixture()
    x.verify_disposable_topology(network, host, sha)

@pytest.mark.parametrize('fault', ['network', 'peer', 'server', 'image', 'state',
                                   'ports', 'mounts', 'data', 'memory', 'cpu', 'auth'])
def test_unsafe_topology_is_rejected(fault):
    x, net, host, sha = fixture()
    if fault == 'network': net['Internal'] = False
    if fault == 'peer': net['Containers']['id2'] = {'Name': 'unapproved'}
    if fault == 'server': host['Name'] = '/wrong-server'
    if fault == 'image': host['Image'] = 'sha256:' + 'b' * 64
    if fault == 'state': host['State']['Running'] = False
    if fault == 'ports': host['HostConfig']['PortBindings'] = {'7687/tcp': [{}]}
    if fault == 'mounts': host['Mounts'] = [{'Type': 'volume'}]
    if fault == 'data': host['HostConfig']['Tmpfs'].pop('/data')
    if fault == 'memory': host['HostConfig']['Memory'] *= 2
    if fault == 'cpu': host['HostConfig']['NanoCpus'] *= 2
    if fault == 'auth': host['Config']['Env'] = []
    with pytest.raises(ValueError): x.verify_disposable_topology(net, host, sha)

@pytest.mark.parametrize('script', ['trace_async_neo4j_cancel_canary.py',
                                    'trace_fenced_adapter_real_neo4j_canary.py'])
def test_only_digest_pinned_disposable_research_commands(script):
    x = subject(); image = 'sha256:' + 'a' * 64
    cmd = x.client_command(image, script)
    assert cmd[cmd.index('--network') + 1] == x.NETWORK
    assert cmd[cmd.index('--pull') + 1] == 'never'
    assert cmd[cmd.index('--cpus') + 1] == '0.4'
    assert cmd[cmd.index('--memory') + 1] == '512m'
    assert '--read-only' in cmd and '--cap-drop' in cmd
    assert cmd[-2] == image
    mounts = [cmd[i + 1] for i, token in enumerate(cmd) if token == '--mount']
    assert len(mounts) == 2 and all('readonly' in m for m in mounts)
    assert all('/work/' in m for m in mounts)
    assert all('/nas' not in m for m in mounts)
    for forbidden in ('--publish', '-p', '--volume', '-v', '--privileged'):
        assert forbidden not in cmd

def test_mutable_image_and_unapproved_source_rejected():
    x = subject()
    with pytest.raises(ValueError):
        x.client_command('some-tag', x.SCRIPTS[0][1])
    with pytest.raises(ValueError):
        x.client_command('sha256:' + 'b' * 64, 'not-approved.py')

def test_exact_read_only_queries_bounded_by_server_timeout():
    for name in ('trace_async_neo4j_cancel_canary.py',
                 'trace_fenced_adapter_real_neo4j_canary.py'):
        text = (ROOT / 'scripts' / name).read_text()
        assert 'timeout=3.0' in text
        assert 'READ_ACCESS' in text
        assert 'SHOW TRANSACTIONS' in text
        assert 'syntheticcancel' in text
        assert 'CREATE (' not in text and 'MERGE (' not in text
