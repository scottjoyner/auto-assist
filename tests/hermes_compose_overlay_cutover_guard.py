#!/usr/bin/env python3
"""Deny-only Hermes cutover gate: do not layer safe binds onto legacy Compose.

Reads only declarative Compose mount metadata and pinned file hashes; never
reads environment values or credentials and never creates Docker objects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import yaml

ROOT = Path('/home/scott/git/auto-assist-deploy-20261016')
BASE = ROOT / 'docker-compose.yml'
OVERRIDE = ROOT / 'compose.override.yml'
EXPECTED_SHA = {
    BASE.name: 'c598217dde96831ec55cd2a0bb0ddcdcb9a76743abb3063af0e0d0997e6fac1d',
    OVERRIDE.name: 'd726a3a2b98ad16bec53168cb586638e022e64ce71933fc9e84462928e8814a0',
}
LEGACY_TARGETS = frozenset({
    '/app/src', '/root/knowledge', '/media/scott/NAS5',
    '/media/scott/SSD_4TB', '/root/.ssh/id_hermes',
    '/root/.ssh/config', '/root/.ssh/id_hermes.pub',
    '/home/scott/git/auto-assist', '/home/scott/git/auto-router',
    '/app/hermes-home', '/root/.hermes/mcp',
    '/home/scott/git/auto-assign', '/home/scott/git/hermes-agent',
    '/home/scott/git/lms', '/usr/local/bin/opencode',
    '/media/scott/SSD_4TB/hermes-home/.hermes/SOUL.md',
})
RESTRICTED_TARGETS = frozenset({'/research/knowledge', '/workspace'})


class Denied(ValueError):
    pass


def require(ok: bool, why: str) -> None:
    if not ok:
        raise Denied(why)


def parse_mounts(service: dict, *, allow_legacy_relative: bool = False) -> dict[str, tuple[str, bool, bool]]:
    """Parse mount fields only; fail closed on unknown/ambiguous syntax."""
    require(type(service) is dict, 'service missing')
    rows = service.get('volumes', [])
    require(type(rows) is list and len(rows) < 100, 'mount list missing or unbounded')
    found = {}
    for item in rows:
        if type(item) is str:
            parts = item.split(':')
            require(len(parts) in (2, 3) and
                    (len(parts)==2 or parts[2] in ('rw','ro')),
                    'unknown string bind format')
            source, dst = parts[:2]
            writable = len(parts) == 2 or parts[2] == 'rw'
            no_create = False
        else:
            require(type(item) is dict and item.get('type') == 'bind'
                    and set(item).issubset({'type','source','target','read_only','bind'}),
                    'unreviewed mount keys/type')
            source, dst = item.get('source'), item.get('target')
            require(type(item.get('read_only', False)) is bool,
                    'ambiguous read_only flag')
            writable = not item.get('read_only', False)
            bind = item.get('bind', {})
            require(type(bind) is dict and set(bind).issubset({'create_host_path'})
                    and type(bind.get('create_host_path', True)) is bool,
                    'unreviewed bind options')
            no_create = bind.get('create_host_path') is False
        require(type(source) is str and
                (source.startswith('/') or
                 (allow_legacy_relative and source == './src' and dst == '/app/src')) and
                type(dst) is str and dst.startswith('/') and dst != '/' and
                '..' not in dst.split('/') and '//' not in dst and dst not in found,
                'invalid, nonabsolute or duplicate bind')
        found[dst] = (source, writable, no_create)
    return found


def inspect_legacy_compose(base: dict, override: dict) -> dict:
    """Prove an overlay onto current base cannot eliminate existing mounts."""
    require(type(base) is dict and type(override) is dict,
            'invalid Compose documents')
    base_service = base.get('services', {}).get('hermes-adapter')
    override_service = override.get('services', {}).get('hermes-adapter', {})
    legacy = parse_mounts(base_service, allow_legacy_relative=True)
    changed = parse_mounts(override_service)
    require(set(legacy) == LEGACY_TARGETS, 'base source inventory drift')
    require(not changed or set(changed).isdisjoint(LEGACY_TARGETS),
            'live override is changing protected legacy bindings')
    return {'legacy_mount_count': len(legacy),
            'legacy_writable_count': sum(v[1] for v in legacy.values()),
            'live_override_mount_count': len(changed),
            'adding_two_restricted_binds_would_leave_legacy_mounts': True,
            'overlay_on_production_compose_authorized': False,
            'standalone_replacement_required': True}


def inspect_effective_replacement(service: dict) -> dict:
    """Deny carryover of legacy mounts in a prospective standalone service.

    A clean two-bind declaration is necessary, not deployment authorization.
    """
    mounts = parse_mounts(service)
    require(set(mounts) == RESTRICTED_TARGETS and
            not set(mounts).intersection(LEGACY_TARGETS),
            'restricted replacement must have exactly two isolated binds')
    read_source, read_rw, read_no_create = mounts['/research/knowledge']
    write_source, write_rw, write_no_create = mounts['/workspace']
    require(not read_rw and write_rw and read_no_create and write_no_create,
            'input must be RO and output RW; auto-create must be disabled')
    def too_broad(source: str) -> bool:
        components=Path(source).parts
        if source in ('/', '/nas', '/home', '/media', '/root', '/tmp'):
            return True
        if source.startswith('/nas/') and len(components) < 4:
            return True
        if any(part in ('.','..') for part in components):
            return True
        return False

    require(read_source != write_source and
            not too_broad(read_source) and not too_broad(write_source) and
            not (read_source.startswith('/media/scott/SSD_4TB') or
                 write_source.startswith('/media/scott/SSD_4TB') or
                 read_source.startswith('/media/scott/NAS5') or
                 write_source.startswith('/media/scott/NAS5') or
                 read_source.startswith('/home/scott/git') or
                 write_source.startswith('/home/scott/git') or
                 read_source.startswith('/root/.ssh') or
                 write_source.startswith('/root/.ssh')),
            'protected/legacy host source reintroduced')
    require(not set(service).intersection({
        'build','env_file','privileged','devices','volumes_from',
        'extra_hosts', 'ports','secrets'}),
        'standalone candidate has unreviewed privileged/runtime capabilities')
    require(service.get('network_mode') in (None,'none') and
            service.get('read_only') is True and
            service.get('user') == '1000:1000' and
            service.get('cap_drop') == ['ALL'] and
            'no-new-privileges:true' in service.get('security_opt', []) and
            type(service.get('image')) is str and
            service['image'].startswith('sha256:') and
            len(service['image']) == 71 and
            all(c in '0123456789abcdef' for c in service['image'][7:]),
            'standalone restricted research profile is not pinned/isolated')
    return {'effective_mount_count':2,
            'legacy_mounts_present':False,
            'host_auto_creation_disabled':True,
            'mount_shape_qualified':True,
            'standalone_image_signature_verified':False,
            'owner_authorized_storage_verified':False,
            'production_deployment_authorized':False}


def physical_readonly() -> dict:
    require(os.uname().nodename == 'x1-370' and os.geteuid() == 1000,
            'read-only qualification allowed on x1-370 nonroot only')
    docs = {}
    for path in (BASE, OVERRIDE):
        require(path.is_file() and not path.is_symlink(),
                'Compose file missing/redirected')
        raw = path.read_bytes()
        require(len(raw) < 200000 and
                hashlib.sha256(raw).hexdigest() == EXPECTED_SHA[path.name],
                'exact deployed Compose source changed')
        docs[path.name] = yaml.safe_load(raw)
    proof = inspect_legacy_compose(docs[BASE.name], docs[OVERRIDE.name])
    return {'decision':'HOLD', 'physical_readonly':True,
            'production_modified':False, 'production_deployment_authorized':False,
            'source_sha256': dict(EXPECTED_SHA), 'compose_source_verified':True,
            'environment_values_read':False, 'protected_corpus_files_opened':0,
            'docker_operations_executed':0, **proof}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--physical-readonly', action='store_true')
    opt = parser.parse_args()
    report = physical_readonly() if opt.physical_readonly else {
        'decision':'HOLD', 'physical_executed':False,
        'production_modified':False, 'production_deployment_authorized':False}
    print(json.dumps(report,sort_keys=True))
