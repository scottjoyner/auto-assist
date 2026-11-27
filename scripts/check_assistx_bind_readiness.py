#!/usr/bin/env python3
"""Read-only deployment mount identity witness; no repair or restart authority."""
import json
import stat
import subprocess
from pathlib import Path

BASE = Path('/home/scott/git/auto-assist-deploy-20261016')
COMMON = {'/app/src': ('src', 'directory'), '/app/templates': ('templates', 'directory'), '/app/artifacts': ('artifacts', 'directory'), '/app/.assistx_cache.sqlite': ('.assistx_cache.sqlite', 'file')}
EXPECTED = {'assistx-api': COMMON, 'assistx-worker': COMMON, 'assistx-hermes-adapter': {'/app/src': COMMON['/app/src']}}


def host_identity(path):
    s = path.lstat()
    kind = 'directory' if stat.S_ISDIR(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'other'
    return (s.st_dev, s.st_ino, kind)


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=5, check=True).stdout


def container_identity(name, destination):
    # Stat, not application import: avoid side effects and secret-bearing logs.
    raw = run(['docker', 'exec', name, 'stat', '-c', '%d:%i:%f', destination]).strip()
    dev, ino, mode = raw.split(':')
    mode = int(mode, 16)
    return (int(dev), int(ino), 'directory' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else 'other')


def check_mount(name, destination, relative, kind, mounts):
    row = {'container': name, 'destination': destination}
    path = BASE / relative
    candidates = [m for m in mounts if m.get('Destination') == destination]
    if len(candidates) != 1 or candidates[0].get('Type') != 'bind' or candidates[0].get('Source') != str(path):
        return {**row, 'status': 'mount_contract_mismatch'}
    try:
        before = host_identity(path)
    except OSError:
        return {**row, 'status': 'host_path_unavailable'}
    if before[2] != kind:
        return {**row, 'status': 'host_type_mismatch'}
    try:
        live = container_identity(name, destination)
        after = host_identity(path)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {**row, 'status': 'identity_unavailable'}
    if before != after:
        return {**row, 'status': 'host_identity_changed'}
    if live[2] != kind:
        return {**row, 'status': 'live_type_mismatch'}
    return {**row, 'status': 'match' if live == before else 'stale_live_mount'}


def collect():
    rows = []
    for name, expected in EXPECTED.items():
        try:
            # Only bind mount metadata is obtained; never inspect container env.
            mounts = json.loads(run(['docker', 'inspect', '--format', '{{json .Mounts}}', name]))
            if not isinstance(mounts, list) or not all(isinstance(m, dict) for m in mounts):
                raise ValueError('invalid mount inventory')
        except (OSError, ValueError, subprocess.SubprocessError):
            rows.extend({'container': name, 'destination': d, 'status': 'mount_inventory_unavailable'} for d in expected)
            continue
        rows.extend(check_mount(name, d, rel, kind, mounts) for d, (rel, kind) in expected.items())
    return {'schema': 'assistx-bind-readiness-v1', 'ready': bool(rows) and all(r['status'] == 'match' for r in rows), 'mounts': rows, 'scope': 'bind identity only; not full service or projection readiness'}


def main():
    result = collect()
    print(json.dumps(result, indent=2))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
