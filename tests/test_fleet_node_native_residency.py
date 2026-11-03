"""Only native-confirmed loaded chat models can become AssistX LLM capacity."""
import pytest
from assistx import fleet_node_agent as agent


def _model(key, typ='llm', resident=True):
    return {'key':key,'type':typ,'loaded_instances':[{'id':'inst'}] if resident else []}


def _caps(monkeypatch, response, *, extra=''):
    calls=[]
    def fake_http(method,url,**kw):
        calls.append((method,url))
        return response
    monkeypatch.setattr(agent,'_http',fake_http)
    monkeypatch.setattr(agent.subprocess,'run',lambda *a,**kw:type('Proc',(),{'returncode':1})())
    monkeypatch.setenv('FLEET_CAPABILITIES',extra)
    result=agent._detect_capabilities('http://local:1234')
    return result,calls


def test_native_loaded_chat_only_drops_catalog_and_embedding(monkeypatch):
    (caps,models),calls=_caps(monkeypatch,(200,{'models':[
        _model('loaded-chat'),_model('downloaded',resident=False),
        _model('nomic-embed',typ='embedding'),
        {'key':'invalid','type':'llm','loaded_instances':['not-an-instance']},
        {'key':'no-instance-id','type':'llm','loaded_instances':[{}]},
        {'key':'empty-instance-id','type':'llm','loaded_instances':[{'id':'  '}]},
        {'key':'non-string-instance-id','type':'llm','loaded_instances':[{'id':7}]},
    ]}))
    assert models==['loaded-chat'] and 'llm' in caps and 'lmstudio' in caps
    assert calls==[('GET','http://local:1234/api/v1/models')]


def test_embedding_only_cannot_advertise_llm_even_via_environment(monkeypatch):
    (caps,models),_=_caps(monkeypatch,(200,{'models':[_model('embed',typ='embedding')]}),
                            extra='llm,lmstudio,python3')
    assert models==[] and 'llm' not in caps and 'lmstudio' not in caps
    assert 'python3' in caps


@pytest.mark.parametrize('response',[(404,{}),(200,{'data':[{'id':'downloaded'}]}),
    (200,{'models':'bad'}),(200,{'models':[]}),
    (200,{'models':[{'key':'phantom','type':'llm','loaded_instances':None}]}),
    (200,{'models':[_model('only-downloaded',resident=False)]})])
def test_non_authoritative_or_unloaded_inventory_is_not_capacity(monkeypatch,response):
    (caps,models),_= _caps(monkeypatch,response)
    assert models==[] and 'llm' not in caps
    assert agent._default_model(None)==''


def test_default_model_is_exact_native_resident_chat_not_first_catalog(monkeypatch):
    def http(method,url,**kw):
        assert url.endswith('/api/v1/models') and method=='GET'
        return 200,{'models':[_model('embedding',typ='embedding'),
             _model('b-unloaded',resident=False),_model('a-chat')]}
    monkeypatch.setattr(agent,'_http',http)
    assert agent._default_model('http://local')=='a-chat'


def test_native_loaded_must_have_nonempty_key_and_valid_instance(monkeypatch):
    (caps,models),_=_caps(monkeypatch,(200,{'models':[
        {'type':'llm','key':'  ','loaded_instances':[{'id':'x'}]},
        {'type':'llm','key':'x','loaded_instances':[]},
        {'type':'llm','key':'valid','loaded_instances':[{'id':'y'}]},
        {'type':'embedding','key':'emb','loaded_instances':[{'id':'e'}]}]}))
    assert models==['valid'] and 'llm' in caps


@pytest.mark.parametrize('resident,requested,should_generate',[
    ([], 'm1', False),
    (['other'], 'm1', False),
    (['m1','other'], 'm1', True),
    (['m1','other'], '', False),
    (['m1'], '', True),
    (['m1'], 'm1', True),
])
def test_claimed_task_may_generate_only_with_exact_resident_model(
    monkeypatch,resident,requested,should_generate,
):
    calls=[]
    def fake_http(method,url,**kwargs):
        calls.append((method,url,kwargs))
        if url.endswith('/claim'):
            return 200,{'claimed':True,'task':{
                'id':'task-1','claimed_by':'node-a','status':'CLAIMED',
                'claim_id':'fence-1','agent_session_id':kwargs['data']['session_id'],
                'task_type':'llm','payload_json':__import__('json').dumps({
                    'model':requested,'prompt':'Evaluate failed task telemetry'})}}
        if url.endswith('/v1/chat/completions'):
            assert should_generate
            assert kwargs['data']['model'] in resident
            return 200,{'choices':[{'finish_reason':'stop',
                         'message':{'content':'Engineering review completed'}}]}
        if url.endswith('/complete'):
            assert kwargs['data']['status']==('DONE' if should_generate else 'FAILED')
            assert kwargs['data']['claim_id']=='fence-1'
            if not should_generate:
                assert kwargs['data']['result']['error']=='claimed_model_not_native_resident'
            return 200,{'task':{'id':'task-1','status':kwargs['data']['status']}}
        raise AssertionError('Unrecognized network request')
    monkeypatch.setattr(agent,'_http',fake_http)
    monkeypatch.setattr(agent,'_native_loaded_chat_models',lambda *_:resident)
    outcome=agent._claim_and_run(assistx_url='http://assistx',router_url='http://router',
        auth=('operator','synthetic'),node_id='node-a',caps=['llm'],
        task={'id':'task-1'},lmstudio_url='http://local:1234')
    assert outcome is None
    assert len([row for row in calls if row[1].endswith('/v1/chat/completions')])==int(should_generate)
    assert len([row for row in calls if row[1].endswith('/complete')])==1


def test_benchmark_task_uses_exact_native_model_gate_before_execution(monkeypatch):
    def fake_http(method,url,**kwargs):
        if url.endswith('/claim'):
            return 200,{'claimed':True,'task':{'id':'benchmark','claimed_by':'node-a',
                 'status':'CLAIMED','claim_id':'fence-b','agent_session_id':kwargs['data']['session_id'],
                 'task_type':'llm',
                 'payload_json':'{"benchmark":true,"model":"unavailable","prompt":"test"}'}}
        if url.endswith('/complete'):
            assert kwargs['data']['status']=='FAILED'
            return 200,{'task':{'id':'benchmark','status':'FAILED'}}
        raise AssertionError('Benchmark must not send traffic to absent model')
    monkeypatch.setattr(agent,'_http',fake_http)
    monkeypatch.setattr(agent,'_native_loaded_chat_models',lambda *_:['resident'])
    monkeypatch.setattr(agent,'execute_task',lambda *args,**kw:pytest.fail('no benchmark inference'))
    assert agent._claim_and_run(assistx_url='http://assistx',router_url='http://router',
        auth=('operator','synthetic'),node_id='node-a',caps=['llm'],
        task={'id':'benchmark'},lmstudio_url='http://local') is None


def test_ambiguous_native_default_is_not_implicitly_chosen(monkeypatch):
    monkeypatch.setattr(agent,'_native_loaded_chat_models',lambda *_:['a','b'])
    assert agent._default_model('http://local')==''
    monkeypatch.setattr(agent,'_http',lambda *args,**kw:pytest.fail('no inference or cache request'))
    outcome=agent.execute_task({'required_capabilities':['llm'],
       'payload':{'prompt':'Inspect routing drift'}},'http://local','.')
    assert outcome['status']=='FAILED'
    assert outcome['result']['reason']=='no_unique_native_resident_chat_model'


def test_unique_native_default_is_used_for_unpinned_direct_llm(monkeypatch):
    monkeypatch.setattr(agent,'_native_loaded_chat_models',lambda *_:['only-chat'])
    sent=[]
    def http(method,url,**kwargs):
        assert url.endswith('/v1/chat/completions')
        sent.append(kwargs['data'])
        return 200,{'choices':[{'message':{'content':'Reviewed'}}]}
    monkeypatch.setattr(agent,'_http',http)
    outcome=agent.execute_task({'required_capabilities':['llm'],
       'payload':{'prompt':'Inspect task supply'}},'http://local','.')
    assert outcome['status']=='DONE' and len(sent)==1 and sent[0]['model']=='only-chat'


@pytest.mark.parametrize('before,claimed_model', [
    (['m1'], 'm1'),
    (['m1', 'm2'], ''),
])
def test_model_eviction_between_poll_and_claim_blocks_generation(
    monkeypatch, before, claimed_model,
):
    """A stale poll result cannot authorize a generation after claim-time eviction."""
    observations = []
    sent = []

    def fake_http(method, url, **kwargs):
        if method == 'GET' and url.endswith('/api/v1/models'):
            observations.append('poll' if not observations else 'after-claim')
            models = before if len(observations) == 1 else []
            return 200, {'models': [_model(key) for key in models]}
        if url.endswith('/claim'):
            return 200, {'claimed': True, 'task': {
                'id': 't', 'claimed_by': 'node-a', 'status': 'CLAIMED',
                'claim_id': 'fence-c',
                'agent_session_id': kwargs['data']['session_id'],
                'task_type': 'llm',
                'payload_json': __import__('json').dumps({
                    'model': claimed_model, 'prompt': 'Review a production code change'})}}
        if url.endswith('/v1/chat/completions'):
            sent.append(kwargs['data'])
            raise AssertionError('stale availability must not dispatch inference')
        if url.endswith('/complete'):
            assert kwargs['data']['status'] == 'FAILED'
            assert kwargs['data']['result']['error'] == 'claimed_model_not_native_resident'
            assert kwargs['data']['claim_id'] == 'fence-c'
            return 200, {'task': {'id': 't', 'status': 'FAILED'}}
        raise AssertionError(f'unexpected request {url}')

    monkeypatch.setattr(agent, '_http', fake_http)
    monkeypatch.setattr(agent.subprocess, 'run',
        lambda *_args, **_kw: type('Proc', (), {'returncode': 1})())
    monkeypatch.delenv('FLEET_CAPABILITIES', raising=False)
    caps, models = agent._detect_capabilities('http://local')
    assert 'llm' in caps and models == sorted(before)
    outcome = agent._claim_and_run(
        assistx_url='http://assistx', router_url='http://router',
        auth=('operator', 'synthetic'), node_id='node-a', caps=caps,
        task={'id': 't'}, lmstudio_url='http://local')
    assert outcome is None
    assert observations == ['poll', 'after-claim']
    assert sent == []
