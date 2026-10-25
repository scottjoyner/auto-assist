"""Offline tests: additive Compose overrides cannot remove Hermes old mounts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import hermes_compose_overlay_cutover_guard as guard
import pytest


def legacy_docs():
    targets=sorted(guard.LEGACY_TARGETS)
    entries=[]
    for i,dest in enumerate(targets):
        source='./src' if dest=='/app/src' else '/synthetic/legacy-'+str(i)
        entries.append(f'{source}:{dest}:{"rw" if i<9 else "ro"}')
    return {'services':{'hermes-adapter':{'volumes':entries}}}, {
        'services':{'hermes-adapter':{}}}


def restricted():
    return {'image':'sha256:'+'a'*64, 'user':'1000:1000',
            'network_mode':'none','read_only':True,
            'cap_drop':['ALL'], 'security_opt':['no-new-privileges:true'],
            'volumes':[
                {'type':'bind','source':'/synthetic/signed-source',
                 'target':'/research/knowledge','read_only':True,
                 'bind':{'create_host_path':False}},
                {'type':'bind','source':'/synthetic/owned-output',
                 'target':'/workspace','read_only':False,
                 'bind':{'create_host_path':False}},
            ]}


def test_default_no_docker_and_hold():
    r=subprocess.run([sys.executable,str(Path(guard.__file__))],
                     check=True,capture_output=True,text=True,timeout=7)
    output=json.loads(r.stdout)
    assert output['decision']=='HOLD'
    assert output['production_modified'] is False
    assert output['physical_executed'] is False


def test_legacy_compose_remains_unsafe_when_new_binds_appear_in_override():
    base,override=legacy_docs()
    report=guard.inspect_legacy_compose(base,override)
    assert report['legacy_mount_count']==16
    assert report['legacy_writable_count']==9
    assert report['standalone_replacement_required'] is True
    assert report['overlay_on_production_compose_authorized'] is False
    override['services']['hermes-adapter']['volumes']=restricted()['volumes']
    report=guard.inspect_legacy_compose(base,override)
    assert report['live_override_mount_count']==2
    assert report['adding_two_restricted_binds_would_leave_legacy_mounts'] is True
    assert report['overlay_on_production_compose_authorized'] is False


def test_restricted_replacement_shape_never_authorizes_deployment():
    report=guard.inspect_effective_replacement(restricted())
    assert report['effective_mount_count']==2
    assert report['legacy_mounts_present'] is False
    assert report['host_auto_creation_disabled'] is True
    assert report['standalone_image_signature_verified'] is False
    assert report['owner_authorized_storage_verified'] is False
    assert report['production_deployment_authorized'] is False


@pytest.mark.parametrize('target',sorted(guard.LEGACY_TARGETS))
def test_reintroduced_legacy_destination_denied(target):
    service=restricted()
    service['volumes'].append({
        'type':'bind','source':'/synthetic/legacy',
        'target':target,'read_only':True,
        'bind':{'create_host_path':False}})
    with pytest.raises(guard.Denied,match='exactly two'):
        guard.inspect_effective_replacement(service)


@pytest.mark.parametrize('bad',[
    ('missing_input',None),('missing_output',None),
    ('wrong_input_rw',True),('wrong_output_ro',True),
    ('create_input',True),('create_output',True),
    ('omit_create_input',None),('omit_create_output',None),
    ('root_input','/'),('nas_root_output','/nas'),
    ('nas_wide','/nas/personal'),('nas_label','/media/scott/NAS5'),
    ('ssd_root','/media/scott/SSD_4TB'),
    ('writable_repo','/home/scott/git/auto-assist'),
    ('duplicate_source',None),('unreviewed_capability',None),
    ('wrong_user',None),('writable_rootfs',None),
    ('unverified_image',None),('host_network',None),
])
def test_candidate_shape_is_fail_closed(bad):
    tag,value=bad
    c=restricted()
    a,b=c['volumes']
    if tag=='missing_input':
        c['volumes']=[b]
    if tag=='missing_output':
        c['volumes']=[a]
    if tag=='wrong_input_rw':
        a['read_only']=False
    if tag=='wrong_output_ro':
        b['read_only']=True
    if tag=='create_input':
        a['bind']['create_host_path']=True
    if tag=='create_output':
        b['bind']['create_host_path']=True
    if tag=='omit_create_input':
        a.pop('bind')
    if tag=='omit_create_output':
        b.pop('bind')
    if tag=='root_input':
        a['source']=value
    if tag=='nas_root_output':
        b['source']=value
    if tag=='nas_wide':
        b['source']=value
    if tag=='nas_label':
        b['source']=value
    if tag=='ssd_root':
        a['source']=value
    if tag=='writable_repo':
        b['source']=value
    if tag=='duplicate_source':
        b['source']=a['source']
    if tag=='unreviewed_capability':
        c['env_file']=['/tmp/secret.env']
    if tag=='wrong_user':
        c['user']='0:0'
    if tag=='writable_rootfs':
        c['read_only']=False
    if tag=='unverified_image':
        c['image']='latest'
    if tag=='host_network':
        c['network_mode']='host'
    with pytest.raises(guard.Denied):
        guard.inspect_effective_replacement(c)


@pytest.mark.parametrize('mutation', ['missing_mount','additional_mount','rewrite_private_ssh','invalid_source'])
def test_legacy_source_inventory_drift_or_unsafe_override_denied(mutation):
    base,override=legacy_docs()
    volumes=base['services']['hermes-adapter']['volumes']
    if mutation=='missing_mount':
        volumes.pop()
    elif mutation=='additional_mount':
        volumes.append('/tmp/other:/unreviewed:rw')
    elif mutation=='rewrite_private_ssh':
        override['services']['hermes-adapter']['volumes']=[
            '/tmp/shadow:/root/.ssh/id_hermes:ro']
    else:
        volumes[0]='../../untrusted:/somewhere:rw'
    with pytest.raises(guard.Denied):
        guard.inspect_legacy_compose(base,override)


def test_guard_never_reads_credentials_or_mutates_production():
    source=Path(guard.__file__).read_text()
    assert 'sha256(raw).hexdigest()' in source
    assert "BASE = ROOT / 'docker-compose.yml'" in source
    assert 'docker_operations_executed' in source
    for forbidden in ('docker compose up','docker run','docker restart',
                      'subprocess.run(', 'os.makedirs(', 'Config.Env',
                      'os.system(', 'source.read_text(', 'yaml.dump('):
        assert forbidden not in source
