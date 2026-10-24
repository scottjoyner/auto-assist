#!/usr/bin/env python3
"""Exact standalone Hermes replacement shape; synthetic render only, HOLD.

No production Compose, Docker image/container, provider auth, protected files,
or actual input/output mount is read, started, created, mutated or approved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import hermes_compose_overlay_cutover_guard as legacy
import yaml

ROOT = Path(__file__).resolve().parent
FILE = ROOT / 'hermes_standalone_replacement_research.compose.yaml'
EXPECTED_FILE_SHA256 = '779e934df4b58cbb48bc3203877ed2477aef60aef6a52a56f251b96929331096'
IMAGE = 'sha256:'+'a'*64  # Deliberately NONEXISTENT research image sentinel
CORPUS = '/synthetic/signed-corpus'
SCRATCH = '/synthetic/authorized/workspace'
REMOTE_SHARE = '//192.168.1.202/fileserver'
SERVICE = 'hermes-replacement-research'
BANNED_KEYS = {'ports', 'expose', 'networks', 'depends_on', 'secrets', 'env_file',
               'build', 'extra_hosts', 'devices', 'privileged', 'volumes_from',
               'pid', 'ipc', 'labels', 'links', 'container_name'}
ALLOWED_KEYS = {'image','pull_policy','entrypoint','command','restart','init',
                'user','read_only','network_mode','cap_drop','security_opt',
                'pids_limit','mem_limit','cpus','tmpfs','environment',
                'volumes','profiles'}
ENVIRONMENT = {
    'HOME':'/workspace','HERMES_HOME':'/workspace/hermes-home',
    'HERMES_CONFIG_HOME':'/workspace/.hermes',
    'PYTHONPATH':'/workspace:/app/src',
    'ASSISTX_OUTBOX_DB':'/workspace/outbox.sqlite',
    'ASSISTX_URL':'http://127.0.0.1:1',
    'ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN':'SYNTHETIC_BOOTSTRAP_ONLY',
    'ASSISTX_STRICT_EXECUTOR_AUTH':'true',
    'ASSISTX_REQUIRE_OWNER_BINDING':'true',
    'HERMES_REQUIRE_OWNER_BOUND_LAUNCH':'true',
    'ASSISTX_EXECUTOR_VERIFY_KEY_FILE':'/app/attestation/executor.pub',
    'HERMES_AGENT_ID':'hermes-replacement-research-only',
    'HERMES_SELFTASK_ENABLED':'false',
    'HERMES_KNOWLEDGE_READ_ROOT':'/research/knowledge',
    'HERMES_KNOWLEDGE_WRITE_ROOT':'/workspace/knowledge',
    'HERMES_REQUIRE_MOUNT_ISOLATION':'true',
    'HERMES_REQUIRE_SIGNED_CORPUS':'true',
    'HERMES_REQUIRE_CORPUS_CHECKPOINT':'true',
    'HERMES_CORPUS_CHECKPOINT_PATH':'/app/attestation/checkpoint.json',
    'HERMES_CORPUS_WITNESS_KEY_PATH':'/app/attestation/witness.pub',
    'HERMES_CORPUS_TRUST_KEY_PATH':'/app/attestation/trust.pub',
    'HERMES_KNOWLEDGE_WRITE_MOUNT':'/workspace',
    'HERMES_EXPECTED_WRITE_FSTYPE':'cifs',
    'HERMES_EXPECTED_WRITE_SOURCE':REMOTE_SHARE,
    'HERMES_KNOWLEDGE_ROOT':'/workspace/knowledge',
    'HERMES_EVAL_PATH':'/workspace/knowledge/eval.json',
    'PYTHONDONTWRITEBYTECODE':'1',
}


def validate_standalone(doc: dict) -> dict:
    legacy.require(type(doc) is dict and
                   set(doc.get('services',{})) == {SERVICE},
                   'unexpected service or missing standalone container')
    svc=doc['services'][SERVICE]
    legacy.require(type(svc) is dict and not set(svc).intersection(BANNED_KEYS)
                   and set(svc) == ALLOWED_KEYS,
                   'service has missing/unreviewed fields')
    mounts=legacy.inspect_effective_replacement(svc)
    legacy.require(svc['image']==IMAGE and svc['pull_policy']=='never' and
                   svc['entrypoint']==['/entrypoint.sh'] and
                   svc['command']==['python3','-B','-m','assistx.strict_executor_main']
                   and svc['restart']=='no' and svc['init'] is False and
                   svc['network_mode']=='none' and svc['profiles']==['research-only']
                   and svc['security_opt']==['no-new-privileges:true'] and
                   svc['pids_limit']==32 and
                   svc['mem_limit']=='402653184' and svc['cpus']==0.5,
                   'research runtime/inference/CPU/network behavior drifted')
    legacy.require(svc['environment']==ENVIRONMENT and
                   svc['tmpfs']==['/tmp:rw,nosuid,nodev,noexec,size=16777216'],
                   'environment/secret or scratch tmpfs drifted')
    actual=legacy.parse_mounts(svc)
    legacy.require(actual['/research/knowledge']==(CORPUS,False,True) and
                   actual['/workspace']==(SCRATCH,True,True),
                   'host source was not the synthetic research fixture')
    legacy.require(not any('/nas' in x[0] or '/home/scott' in x[0]
                           for x in actual.values()),
                   'physical host path was introduced')
    return {
        'decision':'HOLD', 'standalone_render_qualified':True,
        'effective_service_count':1,
        'effective_mount_count':mounts['effective_mount_count'],
        'production_deployment_authorized':False,
        'independently_signed_image':False,
        'real_source_or_output_authorized':False,
        'real_container_created':False,
        'legacy_unsafe_mounts_present':False,
        'real_provider_or_network_access_enabled':False,
    }


def render_only() -> dict:
    legacy.require(os.uname().nodename=='x1-370' and os.geteuid()==1000,
                   'nonroot x1-370 render-only validation required')
    legacy.require(FILE.is_file() and not FILE.is_symlink(),
                   'research fixture missing or redirected')
    buf=FILE.read_bytes()
    legacy.require(len(buf)<15000 and hashlib.sha256(buf).hexdigest()==EXPECTED_FILE_SHA256,
                   'standalone source bytes changed')
    raw=yaml.safe_load(buf)
    legacy.require(set(raw.get('services',{}))=={SERVICE} and
                   set(raw['services'][SERVICE]).isdisjoint(BANNED_KEYS),
                   'unreviewed static research service')
    cmd=['docker','compose','--env-file','/dev/null',
         '-p','hermes-standalone-static-only','-f',str(FILE),
         '--profile','research-only','config','--format','json']
    env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),
         'HOME':'/home/scott',
         'HERMES_REPLACEMENT_RESEARCH_IMAGE':IMAGE,
         'HERMES_REPLACEMENT_RESEARCH_CIFS_SOURCE':REMOTE_SHARE,
         'HERMES_REPLACEMENT_RESEARCH_RO_PATH':CORPUS,
         'HERMES_REPLACEMENT_RESEARCH_RW_PATH':SCRATCH}
    r=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=15,check=False)
    legacy.require(r.returncode==0 and len(r.stdout)<35000,
                   'bounded Compose render failed')
    result=validate_standalone(json.loads(r.stdout))
    return {**result,'source_sha256':EXPECTED_FILE_SHA256,
            'host_source_paths_selected':False,
            'environment_or_secret_values_read':False,
            'docker_operations_executed':'config_render_only',
            'production_modified':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--render-only',action='store_true')
    args=p.parse_args()
    print(json.dumps(render_only() if args.render_only else {
        'decision':'HOLD','render_executed':False,
        'production_deployment_authorized':False,'production_modified':False,
    },sort_keys=True))
