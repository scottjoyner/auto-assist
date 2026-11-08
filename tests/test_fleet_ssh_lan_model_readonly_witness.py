"""Synthetic offline tests: never open SSH, sockets, models, Tasks or graph."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/fleet_ssh_lan_model_readonly_witness.py'
spec = importlib.util.spec_from_file_location('fleet_ssh_lan_witness', SOURCE)
assert spec and spec.loader
witness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(witness)


def wire(models=('ornith-1.5-35b-a3b-apex-mtp',), **headers):
    body = json.dumps({'data': [{'id': x} for x in models]}, separators=(',', ':')).encode()
    h = {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': str(len(body)),
        'Connection': 'close',
        **headers,
    }
    return (b'HTTP/1.1 200 OK\r\n'
            + b''.join(k.encode() + b': ' + str(v).encode() + b'\r\n' for k,v in h.items())
            + b'\r\n' + body)


def test_strict_http_catalog_exact_model_and_unique_count():
    body = wire(['ornith-1.5-35b-a3b-apex-mtp', 'other', 'other'])
    assert witness.parse_http_catalog(body,'ornith-1.5-35b-a3b-apex-mtp') == 2


@pytest.mark.parametrize('case, reason', [
    (b'', 'invalid_http_framing'),
    (b'HTTP/1.1 403 Forbidden\r\n\r\nblocked', 'http_not_200'),
    (wire(['wrong-model']), 'exact_expected_model_missing'),
    (wire([], **{'Content-Type': 'text/html'}), 'unexpected_content_type'),
    (wire([], **{'Transfer-Encoding': 'chunked'}), 'transfer_encoding_not_supported'),
    (wire([], **{'Content-Length': '999'}), 'body_length_mismatch'),
    (wire([], **{'Content-Length': '-1'}), 'body_length_mismatch'),
    (wire([]) + b'EXTRA', 'body_length_mismatch'),
    (b'HTTP/1.1 200 OK\r\n' + b'Junk: yes\r\n' * 1000 + b'\r\n' + b'{}', 'invalid_http_framing'),
    (b'x' * (64 * 1024 + 1), 'wire_response_oversized'),
] )
def test_malformed_http_catalog_rejected(case, reason):
    with pytest.raises(ValueError, match=reason):
        witness.parse_http_catalog(case,'ornith-1.5-35b-a3b-apex-mtp')


def test_refuses_duplicate_header_even_when_contents_match():
    data=wire()
    data=data.replace(b'Content-Length:', b'Content-Length: 3\r\nContent-Length:', 1)
    with pytest.raises(ValueError,match='duplicate_or_invalid_header'):
        witness.parse_http_catalog(data,'ornith-1.5-35b-a3b-apex-mtp')


def test_refuses_duplicate_json_properties():
    body=b'{"data": [], "data": [{"id": "ornith-1.5-35b-a3b-apex-mtp"}]}'
    data=(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
          +str(len(body)).encode()+b'\r\n\r\n'+body)
    with pytest.raises(ValueError,match='duplicate_json_property'):
        witness.parse_http_catalog(data,'ornith-1.5-35b-a3b-apex-mtp')


def test_refuses_oversized_model_catalog():
    body=wire(['ornith-1.5-35b-a3b-apex-mtp']*257)
    with pytest.raises(ValueError,match='model_catalog_too_large'):
        witness.parse_http_catalog(body,'ornith-1.5-35b-a3b-apex-mtp')


def test_ssh_command_enforces_known_hosts_and_lan_pinning():
    for node,addr in [('xwing','192.168.1.86'),('joyner','192.168.1.79')]:
        cmd=witness.ssh_command(node)
        assert cmd[:4]==['ssh','-T','-F','/dev/null']
        assert 'HostName='+addr in cmd
        assert 'HostKeyAlias='+node in cmd
        assert 'StrictHostKeyChecking=yes' in cmd
        assert 'BatchMode=yes' in cmd
        assert 'ForwardAgent=no' in cmd
        assert 'ProxyCommand=none' in cmd
        assert 'PasswordAuthentication=no' in cmd
        assert 'KbdInteractiveAuthentication=no' in cmd
        assert cmd[-3:]==['-W','127.0.0.1:1234',node]
        assert not any(s in cmd for s in ('-L','-R','-D','-A','-tt','-X','-Y'))
        assert not any('100.' in s for s in cmd)


def test_unknown_node_rejected_without_network():
    with pytest.raises(ValueError, match='node_not_in_reviewed_allowlist'):
        witness.ssh_command('attacker@public-host')
    with pytest.raises(ValueError, match='node_not_in_reviewed_allowlist'):
        witness.check_node('unlisted',reader=lambda _:pytest.fail('network'))


def test_nonadmitting_observation_not_direct_lan_admission():
    observed=witness.check_node('xwing',reader=lambda _:wire())
    assert observed['source_transport']=='verified_ssh_over_physical_lan'
    assert observed['expected_model_present'] is True
    assert observed['direct_lan_http_verified'] is False
    assert observed['production_ingress_authorized'] is False
    assert observed['approved_parallel_slots']==0
    assert observed['task_admission']=='HOLD'
    assert observed['status']=='OBSERVED_NON_ADMITTING'


def test_joyner_requires_exact_model_id():
    with pytest.raises(ValueError,match='exact_expected_model_missing'):
        witness.check_node('joyner',reader=lambda _:wire())
    result=witness.check_node('joyner',reader=lambda _:wire(['joyner-toolcall']))
    assert result['node']=='joyner' and result['advertised_unique_models']==1


def test_default_without_live_never_calls_ssh(monkeypatch,capsys):
    monkeypatch.setattr(witness,'check_node',lambda *_a,**_k:pytest.fail('network'))
    assert witness.main([])==2
    output=json.loads(capsys.readouterr().out)
    assert output['reason']=='LIVE_SSH_PROBES_NOT_REQUESTED'
    assert output['admission_applied'] is False
    assert output['task_admission']=='HOLD'
    assert output['observations']==[]


def test_live_witness_never_admits_even_with_both_valid(monkeypatch,capsys):
    monkeypatch.setattr(witness,'read_ssh_catalog',lambda node:wire([witness.TARGETS[node][1]]))
    # The default reader is captured at definition, so replace check_node explicitly.
    original=witness.check_node
    monkeypatch.setattr(witness,'check_node',lambda node:original(node,reader=lambda n:wire([witness.TARGETS[n][1]])))
    assert witness.main(['--live','--node','all'])==2
    result=json.loads(capsys.readouterr().out)
    assert len(result['observations'])==2
    assert all(x['status']=='OBSERVED_NON_ADMITTING' for x in result['observations'])
    assert result['task_admission']=='HOLD'
    assert result['admission_applied'] is False
    assert result['direct_lan_http_verified'] is False


def test_live_witness_redacts_transport_exception(monkeypatch,capsys):
    def fail(node):raise ValueError('synthetic-secret-token-please-not-log')
    monkeypatch.setattr(witness,'check_node',fail)
    assert witness.main(['--live','--node','xwing'])==2
    result=capsys.readouterr().out
    assert 'synthetic-secret' not in result
    assert json.loads(result)['observations'][0]['reason']=='SSH_LAN_MODEL_WITNESS_FAILED'


def test_no_mutating_or_signed_entrypoints():
    assert not any(hasattr(witness,x) for x in
                   ('approve','apply_projection','renew_lease','claim_task','sign_receipt'))
    assert b'POST ' not in witness.REQUEST
    assert witness.REQUEST.startswith(b'GET /v1/models HTTP/1.1')
