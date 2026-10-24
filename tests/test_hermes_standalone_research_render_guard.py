"""Fail-closed offline contract for standalone *render-only* Hermes research."""
from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import hermes_compose_overlay_cutover_guard as legacy
import hermes_standalone_research_render_guard as guard
import pytest


def fixture():
    return {'services': {guard.SERVICE: {
        'image':guard.IMAGE,
        'pull_policy':'never',
        'entrypoint':['/entrypoint.sh'],
        'command':['python3','-B','-m','assistx.strict_executor_main'],
        'restart':'no', 'init':False, 'user':'1000:1000',
        'read_only':True,'network_mode':'none',
        'cap_drop':['ALL'],
        'security_opt':['no-new-privileges:true'],
        'pids_limit':32,'mem_limit':'402653184','cpus':0.5,
        'tmpfs':['/tmp:rw,nosuid,nodev,noexec,size=16777216'],
        'profiles':['research-only'],
        'environment':dict(guard.ENVIRONMENT),
        'volumes':[
            {'type':'bind','source':guard.CORPUS,
             'target':'/research/knowledge','read_only':True,
             'bind':{'create_host_path':False}},
            {'type':'bind','source':guard.SCRATCH,
             'target':'/workspace','bind':{'create_host_path':False}}
        ]
    }}}


def test_standalone_offline_shape_is_necessary_but_no_release_grant():
    report=guard.validate_standalone(fixture())
    assert report['standalone_render_qualified'] is True
    assert report['effective_mount_count']==2
    assert report['effective_service_count']==1
    assert report['production_deployment_authorized'] is False
    assert report['independently_signed_image'] is False
    assert report['real_source_or_output_authorized'] is False
    assert report['real_container_created'] is False
    assert report['real_provider_or_network_access_enabled'] is False
    assert report['rootless_profile_path_isolated'] is True
    assert report['attestation_artifacts_independently_provisioned'] is False
    assert report['legitimate_task_access_physically_verified'] is False
    assert report['required_attestation_paths'] == sorted([
        '/app/attestation/executor.pub', '/app/attestation/checkpoint.json',
        '/app/attestation/witness.pub', '/app/attestation/trust.pub'])


def test_profiles_path_must_resolve_into_writable_scratch_not_root():
    doc=fixture()
    env=doc['services'][guard.SERVICE]['environment']
    assert env['HERMES_PROFILES_PATH']=='/workspace/.hermes/profiles.yaml'
    for altered in ('/root/.hermes/profiles.yaml',
                    '/research/knowledge/profiles.yaml',
                    '/workspace/knowledge/profiles.yaml',
                    '/workspace/hermes-home/profiles.yaml'):
        bad=fixture()
        bad['services'][guard.SERVICE]['environment']['HERMES_PROFILES_PATH']=altered
        with pytest.raises(legacy.Denied):
            guard.validate_standalone(bad)


def test_embedded_attestation_material_remains_unapproved():
    report=guard.validate_standalone(fixture())
    assert len(report['required_attestation_paths'])==4
    assert not report['attestation_artifacts_independently_provisioned']
    assert not report['production_deployment_authorized']
    for key in ('ASSISTX_EXECUTOR_VERIFY_KEY_FILE',
                'HERMES_CORPUS_CHECKPOINT_PATH',
                'HERMES_CORPUS_WITNESS_KEY_PATH',
                'HERMES_CORPUS_TRUST_KEY_PATH'):
        bad=fixture()
        bad['services'][guard.SERVICE]['environment'][key]='/workspace/untrusted.pub'
        with pytest.raises(legacy.Denied):
            guard.validate_standalone(bad)


def test_default_does_not_use_docker_or_real_paths():
    r=subprocess.run([sys.executable,str(Path(guard.__file__))],
                     capture_output=True,text=True,check=True,timeout=8)
    assert json.loads(r.stdout)=={
        'decision':'HOLD','render_executed':False,
        'production_deployment_authorized':False,'production_modified':False}
    assert guard.IMAGE == 'sha256:'+'a'*64
    assert guard.CORPUS.startswith('/synthetic/')
    assert guard.SCRATCH.startswith('/synthetic/')


@pytest.mark.parametrize('legacy_target',sorted(legacy.LEGACY_TARGETS))
def test_every_old_mount_is_forbidden(legacy_target):
    doc=fixture()
    doc['services'][guard.SERVICE]['volumes'].append({
        'type':'bind','source':'/synthetic/unsafe-legacy',
        'target':legacy_target,'bind':{'create_host_path':False}})
    with pytest.raises(legacy.Denied):
        guard.validate_standalone(doc)


@pytest.mark.parametrize('field,bad',[
    ('network_mode','host'),('read_only',False),('user','0:0'),
    ('restart','always'),('pull_policy','always'),('image','latest'),
    ('entrypoint',['/bin/sh']),('command',['sleep','infinity']),
    ('init',True),('profiles',[]),('cap_drop',[]),
    ('security_opt',[]),('pids_limit',500),('cpus',8.0),
    ('mem_limit','10737418240'),('tmpfs',[]),
])
def test_runtime_change_denied(field,bad):
    doc=fixture()
    doc['services'][guard.SERVICE][field]=bad
    with pytest.raises(legacy.Denied):
        guard.validate_standalone(doc)


@pytest.mark.parametrize('key,bad',[
    ('ASSISTX_URL','http://10.0.3.27:8000'),
    ('ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN','production-like-token'),
    ('ASSISTX_STRICT_EXECUTOR_AUTH','false'),
    ('ASSISTX_REQUIRE_OWNER_BINDING','false'),
    ('HERMES_REQUIRE_OWNER_BOUND_LAUNCH','false'),
    ('HERMES_SELFTASK_ENABLED','true'),
    ('HERMES_REQUIRE_MOUNT_ISOLATION','false'),
    ('HERMES_REQUIRE_SIGNED_CORPUS','false'),
    ('HERMES_REQUIRE_CORPUS_CHECKPOINT','false'),
    ('HERMES_EXPECTED_WRITE_SOURCE','//192.168.1.203/other'),
    ('HERMES_EXPECTED_WRITE_FSTYPE','ext4'),
    ('PYTHONPATH','/tmp/untrusted'),
])
def test_environment_and_source_identity_drift_denied(key,bad):
    doc=fixture()
    doc['services'][guard.SERVICE]['environment'][key]=bad
    with pytest.raises(legacy.Denied):
        guard.validate_standalone(doc)


@pytest.mark.parametrize('extra',[
    'env_file','secrets','ports','networks','privileged','devices',
    'extra_hosts','volumes_from','build','depends_on','container_name',
])
def test_extra_runtime_privilege_or_secret_source_denied(extra):
    doc=fixture()
    doc['services'][guard.SERVICE][extra]=[]
    with pytest.raises(legacy.Denied):
        guard.validate_standalone(doc)


@pytest.mark.parametrize('mutate',[
    'rw_input','ro_output','auto_create_input','auto_create_output',
    'legacy_knowledge','repo_source','nas_root','nas_label','ssd',
    'same_source','more_mounts','missing_mount','credential_mount',
])
def test_bind_source_and_polarity_regressions_denied(mutate):
    doc=fixture()
    v=doc['services'][guard.SERVICE]['volumes']
    a,b=v
    if mutate=='rw_input':
        a['read_only']=False
    elif mutate=='ro_output':
        b['read_only']=True
    elif mutate=='auto_create_input':
        a['bind']['create_host_path']=True
    elif mutate=='auto_create_output':
        b.pop('bind')
    elif mutate=='legacy_knowledge':
        a['target']='/root/knowledge'
    elif mutate=='repo_source':
        b['source']='/home/scott/git/auto-assist'
    elif mutate=='nas_root':
        b['source']='/nas'
    elif mutate=='nas_label':
        b['source']='/media/scott/NAS5'
    elif mutate=='ssd':
        a['source']='/media/scott/SSD_4TB/knowledge'
    elif mutate=='same_source':
        b['source']=a['source']
    elif mutate=='more_mounts':
        v.append({'type':'bind','source':'/tmp/untrusted',
                  'target':'/tmp/untrusted','read_only':True,
                  'bind':{'create_host_path':False}})
    elif mutate=='missing_mount':
        v.pop()
    elif mutate=='credential_mount':
        b['source']='/root/.ssh/id_hermes'
    with pytest.raises(legacy.Denied):
        guard.validate_standalone(doc)


def test_second_service_even_if_restricted_is_denied():
    doc=fixture()
    doc['services']['hermes-adapter']=deepcopy(doc['services'][guard.SERVICE])
    with pytest.raises(legacy.Denied):
        guard.validate_standalone(doc)


def test_template_byte_pin_is_exact_and_no_production_compose_include():
    import hashlib
    buf=guard.FILE.read_bytes()
    assert hashlib.sha256(buf).hexdigest()==guard.EXPECTED_FILE_SHA256
    source=buf.decode()
    assert 'hermes-replacement-research:' in source
    assert 'compose.override' not in source
    assert 'docker-compose.yml' not in source
    assert 'create_host_path: false' in source
    assert '/root/.ssh' not in source
    assert 'HERMES_REPLACEMENT_RESEARCH_RO_PATH' in source
    assert 'HERMES_REPLACEMENT_RESEARCH_RW_PATH' in source
    assert '${HERMES_REPLACEMENT_RESEARCH_CIFS_SOURCE:?' in source


def test_render_guard_has_no_docker_creation_or_provider_operations():
    text=guard.__file__ and Path(guard.__file__).read_text()
    for forbidden in ('docker run','docker create','docker compose up',
                      'docker exec','docker stop','docker restart',
                      'os.makedirs(', 'shutil.copy','requests.post(',
                      'Config.Env','/media/scott/SSD_4TB/knowledge'):
        assert forbidden not in text
