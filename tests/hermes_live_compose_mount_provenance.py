#!/usr/bin/env python3
"""Read-only live Hermes Compose-source and mount-drift witness.

Never executes Compose up/down, reads service environment or filesystem
contents, or claims a currently unsafe production mount layout is approved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import yaml

CONTAINER = 'assistx-hermes-adapter'
DEPLOY = Path('/home/scott/git/auto-assist-deploy-20261016')
SOURCES = ('docker-compose.yml', 'compose.override.yml')
SOURCE_SHAS = {
    'docker-compose.yml': 'c598217dde96831ec55cd2a0bb0ddcdcb9a76743abb3063af0e0d0997e6fac1d',
    'compose.override.yml': 'd726a3a2b98ad16bec53168cb586638e022e64ce71933fc9e84462928e8814a0',
}
IMAGE = 'sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307'
CONFIG_HASH = 'fb3fc78828bd4f54ec7905cfc5e3aa3a7115c1c2f1d4683d0c5077225e93e70d'
PROJECT = 'auto-assist-wt-ab3bb64'
BLOCKERS = (
    'production_broad_rw_ssd_knowledge_aliases',
    'production_mutable_repo_and_application_source',
    'production_nas5_label_is_local_root_directory',
    'private_ssh_mounted_in_live_container',
    'unsigned_replacement_image_and_owner_receipts',
    'production_restart_rollback_not_qualified',
)

class Refuse(ValueError):
    pass


def need(ok, why):
    if not ok:
        raise Refuse(why)


def declared_mounts(compose: dict, deploy: Path) -> dict[str, tuple[str, bool]]:
    need(type(compose) is dict and type(compose.get('services')) is dict,
         'missing Compose services')
    service = compose['services'].get('hermes-adapter')
    need(type(service) is dict and type(service.get('volumes')) is list,
         'missing explicit Hermes binds')
    mounts = {}
    for declaration in service['volumes']:
        need(type(declaration) is str and '\x00' not in declaration,
             'unexpected Compose mount syntax')
        parts = declaration.split(':')
        need(len(parts) in (2, 3) and len(parts[0]) > 0 and
             parts[1].startswith('/') and
             (len(parts) == 2 or parts[2] in ('rw', 'ro')),
             'ambiguous mount declaration')
        source, target = parts[:2]
        need(source.startswith('/') or source.startswith('./'),
             'named volumes and expansion need independent qualification')
        source = (str(deploy / source) if source.startswith('./') else source)
        need(os.path.isabs(source) and os.path.normpath(source) == source and
             os.path.normpath(target) == target and target != '/' and
             target not in mounts, 'noncanonical or duplicate Compose bind')
        mounts[target] = (source, len(parts) == 2 or parts[2] == 'rw')
    need(len(mounts) == 16, 'deployed volume declaration count drift')
    return mounts


def compare(declared: dict, observed: list) -> dict:
    need(type(declared) is dict and type(observed) is list,
         'invalid mount inventory')
    found = {}
    for m in observed:
        need(type(m) is dict and m.get('Type') == 'bind' and
             type(m.get('Source')) is str and type(m.get('Destination')) is str
             and type(m.get('RW')) is bool, 'malformed Docker bind')
        dest = m['Destination']
        need(dest not in found, 'duplicate Docker mount destination')
        found[dest] = (m['Source'], m['RW'])
    need(set(declared) == set(found), 'Compose/Docker mount destinations differ')
    need(all(found[dst] == declared[dst] for dst in declared),
         'Compose/Docker bind source or RW mode differ')
    return {'mount_declarations_match_live': True,
            'mount_count': len(found),
            'writable_mount_count': sum(rw for _src, rw in found.values()),
            'ssd_ancestor_rw': found.get('/media/scott/SSD_4TB') ==
                ('/media/scott/SSD_4TB', True),
            'corpus_alias_rw': found.get('/root/knowledge') ==
                ('/home/scott/knowledge', True),
            'nas_label_rw': found.get('/media/scott/NAS5') ==
                ('/media/scott/NAS5', True),
            'app_source_rw': bool(found.get('/app/src', ('', False))[1])}


def check_labels(labels: dict) -> None:
    need(type(labels) is dict, 'missing Compose labels')
    must = {
        'com.docker.compose.project': PROJECT,
        'com.docker.compose.service': 'hermes-adapter',
        'com.docker.compose.project.working_dir': str(DEPLOY),
        'com.docker.compose.project.config_files':
            ','.join(str(DEPLOY / name) for name in SOURCES),
        'com.docker.compose.config-hash': CONFIG_HASH,
    }
    need(all(labels.get(k) == v for k, v in must.items()),
         'current Compose project or config-hash mismatch')


def read_docker(args: list[str]):
    result = subprocess.run(['docker', 'inspect', '-f', *args, CONTAINER],
                            capture_output=True, text=True, timeout=9)
    need(result.returncode == 0 and len(result.stdout) < 100000,
         'bounded Docker metadata unavailable')
    return result.stdout.strip()


def physical():
    need(os.uname().nodename == 'x1-370' and os.geteuid() == 1000,
         'x1-370 nonroot only')
    sources = {}
    for name in SOURCES:
        path = DEPLOY / name
        need(path.is_file() and not path.is_symlink(), 'Compose source untrusted')
        buf = path.read_bytes()
        need(hashlib.sha256(buf).hexdigest() == SOURCE_SHAS[name],
             'on-disk Compose source drift')
        sources[name] = yaml.safe_load(buf)
    declared = declared_mounts(sources[SOURCES[0]], DEPLOY)
    override = sources[SOURCES[1]].get('services', {}).get('hermes-adapter')
    need(not override or 'volumes' not in override,
         'override unexpectedly changes Hermes mounts')
    labels = json.loads(read_docker(['{{json .Config.Labels}}']))
    check_labels(labels)
    state = read_docker(['{{.Image}} {{.State.Running}}']).split()
    need(state == [IMAGE, 'true'], 'live image or status differs')
    report = compare(declared, json.loads(read_docker(['{{json .Mounts}}'])))
    need(report['mount_count'] == 16 and report['writable_mount_count'] == 9
         and all(report[k] for k in ('ssd_ancestor_rw', 'corpus_alias_rw',
                                    'nas_label_rw', 'app_source_rw')),
         'expected unsafe production baseline has changed')
    report.update(decision='HOLD', source_sha256=dict(SOURCE_SHAS),
                  compose_config_hash_matches_live=True,
                  image_pinned_matches_live=True, production_modified=False,
                  production_release_authorized=False,
                  environment_or_secret_values_read=False,
                  provider_requests_forwarded=0,
                  release_blockers=list(BLOCKERS))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--physical-readonly', action='store_true')
    args = parser.parse_args()
    print(json.dumps(physical() if args.physical_readonly else {
        'decision': 'HOLD', 'physical_executed': False,
        'production_modified': False, 'production_release_authorized': False},
        sort_keys=True))
