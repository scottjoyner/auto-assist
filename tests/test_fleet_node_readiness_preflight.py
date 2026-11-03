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
            return native_status,{'models':[{'key':'exact-model','loaded_instances':[{'id':'i'}] if loaded else []},{'key':'catalog-only','loaded_instances':[]}]} if native_status==200 else {}
        if url.endswith('/v1/models'):return 200,{'data':[{'id':f'catalog-{i}'} for i in range(11)]}
        return 0,{}
    monkeypatch.setattr(a,'_get',fetch)
    monkeypatch.setattr(a,'_service_active',lambda _:True)
    record=a.observation(worker_source=source,credential_file=secret,
      assistx_url='http://private-assistx',model_url='http://local-model',expected_model='exact-model')
    return record,calls


def test_200_catalog_is_not_loaded_model_count(monkeypatch,tmp_path):
    r,c=_observed(monkeypatch,tmp_path)
    assert r['service_active']
    assert r['catalog_entries']==11 and r['native_loaded_entries']==1
    assert r['expected_model_exactly_loaded'] is True
    assert r['read_only_auth_probe_http']==401
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
    result=a._get('http://allowed-tailnet-host:8000/api/agent/tasks?status=READY',credentials=('account','credential'))
    assert result==(302,{})
    handlers=seen[0][1]
    assert any(isinstance(x,a.urllib.request.ProxyHandler) and x.proxies=={} for x in handlers)
    assert a.NoRedirect().redirect_request(None,None,302,'redirect',None,'http://other-host/') is None
    assert seen[1]==('request',True)


def test_oversized_secret_file_rejected(tmp_path):
    p=tmp_path/'env';p.write_text('FLEET_AUTH_USER=a\nFLEET_AUTH_PASS='+'c'*18000+'\n');p.chmod(0o600)
    assert a._credentials(p) is None