"""Deterministic read-only fleet preflight tests; never touches production."""
import importlib.util
import os
from pathlib import Path
import stat
import pytest

p=Path(__file__).resolve().parents[1] / 'scripts' / 'fleet_node_readiness_preflight.py'
spec=importlib.util.spec_from_file_location('fleet_agent_preflight',p)
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def _observed(monkeypatch,tmp_path,*, native_status=200,loaded=True,credential=True):
    source=tmp_path/'agent.py';source.write_text('poll(state=READY&limit=20)\n')
    secret=tmp_path/'agent.env';secret.write_text('FLEET_AUTH_USER=test-user\nFLEET_AUTH_PASS="test-credential"\n')
    secret.chmod(0o600)
    if not credential:
        secret.chmod(0o644)
    calls=[]
    def fetch(url,*,credentials=None):
        calls.append((url,credentials is not None))
        if '/api/agent/tasks?' in url:return 401,{}
        if url.endswith('/api/v1/models'):
            return native_status,{'models':[{'key':'exact-model','type':'llm','loaded_instances':[{'id':'i'}] if loaded else []},{'key':'catalog-only','type':'llm','loaded_instances':[]}]} if native_status==200 else {}
        if url.endswith('/v1/models'):return 200,{'data':[{'id':f'catalog-{i}'} for i in range(11)]}
        return 0,{}
    monkeypatch.setattr(a,'_get',fetch)
    monkeypatch.setattr(a,'_service_active',lambda _:True)
    monkeypatch.setattr(a,'_running_credentials',lambda _:None)
    record=a.observation(worker_source=source,credential_file=secret,
      assistx_url='http://private-assistx',model_url='http://local-model',expected_model='exact-model')
    return record,calls


def test_200_catalog_is_not_loaded_model_count(monkeypatch,tmp_path):
    r,c=_observed(monkeypatch,tmp_path)
    assert r['service_active']
    assert r['catalog_entries']==11 and r['native_loaded_entries']==1
    assert r['expected_model_exactly_loaded'] is True
    assert r['read_only_auth_probe_http']==401
    assert r['matches_live_process_credentials']=='NOT_CHECKED'
    assert r['authorizes_dispatch'] is False and r['approved_runtime_slots']=='NOT_CHECKED'
    assert r['old_state_ready_query'] is True and not r['backoff_implemented']
    assert ('test-user' not in str(r)) and ('test-credential' not in str(r))
    assert len(c)==3 and c[0][1] is True and c[1][1] is False and c[2][1] is False


def test_no_native_api_cannot_confirm_loaded(monkeypatch,tmp_path):
    r,_=_observed(monkeypatch,tmp_path,native_status=404)
    assert r['catalog_http']==200 and r['catalog_entries']==11
    assert r['native_http']==404 and r['native_loaded_entries']==0
    assert r['expected_model_exactly_loaded'] is False
    assert r['native_inventory_authoritative'] is False


def test_native_catalog_no_instances_is_not_loaded(monkeypatch,tmp_path):
    r,_=_observed(monkeypatch,tmp_path,loaded=False)
    assert r['native_inventory_authoritative'] is True
    assert r['native_loaded_entries']==0 and r['expected_model_exactly_loaded'] is False


def test_credential_file_permissions_fail_closed(monkeypatch,tmp_path):
    r,c=_observed(monkeypatch,tmp_path,credential=False)
    assert r['credentials_loaded_from_secure_file'] is False
    assert r['read_only_auth_probe_http'] is None
    assert not any('/api/agent/tasks?' in url for url,_ in c)


def test_reject_secret_symlink(tmp_path):
    dest=tmp_path/'real.env';dest.write_text('FLEET_AUTH_USER=a\nFLEET_AUTH_PASS=b\n');dest.chmod(0o600)
    link=tmp_path/'link.env';link.symlink_to(dest)
    assert a._credentials(link) is None


def test_source_digest_comparison_is_non_authoritative(monkeypatch,tmp_path):
    r,_=_observed(monkeypatch,tmp_path)
    src=tmp_path/'agent.py';secret=tmp_path/'agent.env'
    monkeypatch.setattr(a,'_get',lambda *args,**kw:(0,{}))
    r2=a.observation(worker_source=src,credential_file=secret,
       assistx_url='http://local',model_url='http://local',expected_sha256=r['source_sha256'])
    assert r2['source_matches_expected'] is True
    assert r2['authorizes_dispatch'] is False


def test_bad_auth_url_never_attempts_credentials(monkeypatch):
    # Direct invalid URL is refused before any credential-bearing Request.
    assert a._get('file:///etc/passwd',credentials=('u','p'))==(0,{})
    assert a._get('http://user:pass@example.org',credentials=('u','p'))==(0,{})


def test_auth_probe_disables_inherited_proxy_and_refuses_redirect(monkeypatch):
    seen=[]
    class StubOpener:
        def open(self,request,timeout):
            seen.append(('request',request.headers.get('Authorization') is not None))
            raise a.urllib.error.HTTPError(request.full_url,302,'redirect',{},None)
    def intercept(*handlers):
        seen.append(('handlers',handlers))
        return StubOpener()
    monkeypatch.setattr(a.urllib.request,'build_opener',intercept)
    result=a._get('http://100.78.106.121:8000/api/agent/tasks?status=READY',credentials=('account','credential'))
    assert result==(302,{})
    handlers=seen[0][1]
    assert any(isinstance(x,a.urllib.request.ProxyHandler) and x.proxies=={} for x in handlers)
    assert a.NoRedirect().redirect_request(None,None,302,'redirect',None,'http://other-host/') is None
    assert seen[1]==('request',True)


def test_oversized_secret_file_rejected(tmp_path):
    p=tmp_path/'env';p.write_text('FLEET_AUTH_USER=a\nFLEET_AUTH_PASS='+'c'*18000+'\n');p.chmod(0o600)
    assert a._credentials(p) is None


def test_credential_probe_rejects_public_metadata_and_operator_endpoints(monkeypatch):
    called = []
    monkeypatch.setattr(a.urllib.request, 'build_opener', lambda *args: called.append(args))
    for url in (
        'http://8.8.8.8:8000/api/agent/tasks',
        'https://example.com:443/api/agent/tasks',
        'http://169.254.169.254:80/api/agent/tasks',
        'http://10.0.0.1:8000/api/agent/tasks',
        'http://100.78.106.121:8000/admin/providers',
        'http://100.78.106.121:8000/api/agent/tasks#fragment',
    ):
        assert a._get(url, credentials=('u', 'secret')) == (0, {})
    assert called == []
    assert a._credential_target_allowed(
        'http://100.78.106.121:8000/api/agent/tasks?status=READY'
    )
    assert a._credential_target_allowed('https://10.0.0.1:8000/api/agent/tasks')


def test_matching_effective_process_credentials_share_file_auth_probe(monkeypatch, tmp_path):
    username, password = 'synthetic-user', 'synthetic-strong-credential'
    monkeypatch.setattr(a, '_running_credentials', lambda _: (username, password))
    monkeypatch.setattr(a, '_service_active', lambda _: True)
    source = tmp_path / 'agent.py'
    source.write_text('auth_denials=0')
    secret = tmp_path / 'agent.env'
    secret.write_text(f'FLEET_AUTH_USER={username}\nFLEET_AUTH_PASS={password}\n')
    secret.chmod(0o600)
    calls = []

    def get(url, *, credentials=None):
        if '/api/agent/tasks?' in url:
            calls.append(credentials)
            return 401, {}
        return 404, {}

    monkeypatch.setattr(a, '_get', get)
    record = a.observation(
        worker_source=source, credential_file=secret,
        assistx_url='http://100.78.106.121:8000',
        model_url='http://127.0.0.1:1234',
    )
    assert record['matches_live_process_credentials'] is True
    assert record['live_process_credentials_present'] is True
    assert record['read_only_auth_probe_http'] == 401
    assert record['live_process_read_only_auth_probe_http'] == 401
    assert len(calls) == 1
    assert record['authentication_grants_task_admission'] is False
    assert username not in str(record) and password not in str(record)


def test_effective_process_and_file_auth_are_independently_observed_if_different(
    monkeypatch, tmp_path,
):
    source = tmp_path / 'agent.py'
    source.write_text('valid worker')
    secret = tmp_path / 'agent.env'
    secret.write_text('FLEET_AUTH_USER=user\nFLEET_AUTH_PASS=old-credential\n')
    secret.chmod(0o600)
    monkeypatch.setattr(a, '_running_credentials', lambda _: ('user', 'new-credential'))
    monkeypatch.setattr(a, '_service_active', lambda _: True)
    calls = []

    def get(url, *, credentials=None):
        if '/api/agent/tasks?' in url:
            calls.append(credentials)
            return (401 if credentials[1] == 'old-credential' else 200), {}
        return 404, {}

    monkeypatch.setattr(a, '_get', get)
    record = a.observation(
        worker_source=source, credential_file=secret,
        assistx_url='http://100.78.106.121:8000',
        model_url='http://127.0.0.1:1234',
    )
    assert record['matches_live_process_credentials'] is False
    assert record['read_only_auth_probe_http'] == 401
    assert record['live_process_read_only_auth_probe_http'] == 200
    assert len(calls) == 2
    assert 'old-credential' not in str(record)
    assert 'new-credential' not in str(record)


def test_extracts_only_same_user_fleet_worker_credentials(monkeypatch):
    class ProcStat:
        st_uid = os.geteuid()

    class FakeProc:
        def __init__(self, path):
            self.path = str(path)

        def stat(self):
            return ProcStat()

        def __truediv__(self, filename):
            return FakeProc(self.path + '/' + filename)

        def read_bytes(self):
            if self.path.endswith('cmdline'):
                return b'python3\0/home/scott/fleet-work/run_fleet_node_agent.py\0'
            if self.path.endswith('environ'):
                return b'FLEET_AUTH_USER=u\0FLEET_AUTH_PASS=synthetic-value\0'
            raise AssertionError('unexpected process data')

    class Run:
        returncode = 0
        stdout = '123\n'

    monkeypatch.setattr(a.subprocess, 'run', lambda *args, **kwargs: Run())
    monkeypatch.setattr(a, 'Path', FakeProc)
    assert a._running_credentials('fleet-node-agent.service') == ('u', 'synthetic-value')


def test_unrelated_process_identity_cannot_be_read(monkeypatch):
    class ProcStat:
        st_uid = os.geteuid()

    class FakeProc:
        def __init__(self, path):
            self.path = str(path)

        def stat(self):
            return ProcStat()

        def __truediv__(self, filename):
            return FakeProc(self.path + '/' + filename)

        def read_bytes(self):
            if self.path.endswith('cmdline'):
                return b'python3\0unrelated_daemon.py\0'
            raise AssertionError('must not read unrelated process environment')

    class Run:
        returncode = 0
        stdout = '123\n'

    monkeypatch.setattr(a.subprocess, 'run', lambda *args, **kwargs: Run())
    monkeypatch.setattr(a, 'Path', FakeProc)
    assert a._running_credentials('fleet-node-agent.service') is None


def test_loaded_embedding_is_not_a_loaded_chat_model(monkeypatch, tmp_path):
    source = tmp_path / 'agent.py'
    source.write_text('fleet-node-agent')
    secret = tmp_path / 'agent.env'
    secret.write_text('FLEET_AUTH_USER=u\nFLEET_AUTH_PASS=p\n')
    secret.chmod(0o600)
    monkeypatch.setattr(a, '_service_active', lambda _: False)

    def get(url, *, credentials=None):
        if url.endswith('/api/v1/models'):
            return 200, {'models': [
                {'key': 'embed-only', 'type': 'embedding',
                 'loaded_instances': [{'id': 'e1'}]},
                {'key': 'chat-resident', 'type': 'llm',
                 'loaded_instances': [{'id': 'm1'}]},
            ]}
        return 200, {'data': []}

    monkeypatch.setattr(a, '_get', get)
    result = a.observation(
        worker_source=source, credential_file=secret,
        assistx_url='http://100.78.106.121:8000', model_url='http://127.0.0.1:1234',
        expected_model='embed-only',
    )
    assert result['native_loaded_entries'] == 2
    assert result['native_loaded_chat_entries'] == 1
    assert result['native_loaded_embedding_entries'] == 1
    assert result['expected_model_exactly_loaded'] is False
    assert result['authorizes_dispatch'] is False
