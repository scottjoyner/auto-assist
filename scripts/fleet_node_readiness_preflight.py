#!/usr/bin/env python3
"""Read-only fleet-agent preflight. Does not authorize, sign or dispatch work.

Output contains only aggregate read-only facts; credentials and raw responses
are never displayed, stored or sent to any address other than the configured
AssistX API. Never follow HTTP redirects with Basic Auth.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _get(url: str, *, credentials: tuple[str, str] | None = None) -> tuple[int, dict]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        return 0, {}
    headers = {'Accept': 'application/json'}
    if credentials is not None:
        raw = (credentials[0] + ':' + credentials[1]).encode()
        headers['Authorization'] = 'Basic ' + base64.b64encode(raw).decode('ascii')
    req = urllib.request.Request(url, headers=headers, method='GET')
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=4) as response:
            if response.status != 200:
                return response.status, {}
            try:
                obj = json.load(response)
            except (ValueError, UnicodeError):
                return 200, {}
            return 200, obj if isinstance(obj, dict) else {}
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        return code, {}
    except (OSError, TimeoutError, ValueError, UnicodeError):
        return 0, {}


def _service_active(service: str) -> bool:
    try:
        result = subprocess.run(
            ['systemctl', '--user', 'is-active', service],
            capture_output=True, timeout=3, text=True, check=False)
        return result.returncode == 0 and result.stdout.strip() == 'active'
    except (OSError, subprocess.TimeoutExpired):
        return False


def _credentials(path: Path) -> tuple[str, str] | None:
    # Refuse symlinked/unowned or group/world-readable secret files. Neither
    # file contents nor parsing exceptions are included in diagnostics.
    try:
        if path.is_symlink():
            return None
        st = path.stat()
        if not path.is_file() or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            return None
        pairs = {}
        for line in path.read_text(encoding='utf-8').splitlines():
            item = line.strip()
            if not item or item.startswith('#') or '=' not in item:
                continue
            name, val = item.split('=', 1)
            if name not in {'FLEET_AUTH_USER', 'FLEET_AUTH_PASS'}:
                continue
            token = shlex.split(val)
            pairs[name] = token[0] if len(token) == 1 else ''
        if pairs.get('FLEET_AUTH_USER') and pairs.get('FLEET_AUTH_PASS'):
            return pairs['FLEET_AUTH_USER'], pairs['FLEET_AUTH_PASS']
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def observation(
    *, worker_source: Path, credential_file: Path, assistx_url: str,
    model_url: str, expected_model: str = '', expected_sha256: str = '',
    service: str = 'fleet-node-agent.service',
) -> dict:
    source = ''
    try:
        if worker_source.is_file() and not worker_source.is_symlink():
            source = worker_source.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        pass
    source_hash = hashlib.sha256(source.encode()).hexdigest() if source else None
    credentials = _credentials(credential_file)
    api_http = None
    if credentials is not None:
        api_http, _ = _get(assistx_url.rstrip('/') + '/api/agent/tasks?status=READY&limit=1',
                           credentials=credentials)
    catalog_status, catalog = _get(model_url.rstrip('/') + '/v1/models')
    catalog_rows = catalog.get('data') if isinstance(catalog.get('data'), list) else []
    native_status, native = _get(model_url.rstrip('/') + '/api/v1/models')
    native_rows = native.get('models') if isinstance(native.get('models'), list) else None
    loaded = []
    if native_status == 200 and native_rows is not None:
        for item in native_rows:
            if (isinstance(item, dict) and isinstance(item.get('key'), str)
                    and isinstance(item.get('loaded_instances'), list)
                    and item['loaded_instances']):
                loaded.append(item)
    exact = any(row['key'] == expected_model for row in loaded) if expected_model else None
    return {
        'schema_version': 'fleet-agent-readonly-preflight.v1',
        'service_active': _service_active(service),
        'worker_source_present': bool(source),
        'source_sha256': source_hash,
        'source_matches_expected': source_hash == expected_sha256 if expected_sha256 else None,
        'old_state_ready_query': 'state=READY&limit=20' in source,
        'backoff_implemented': 'auth_denials' in source,
        'credentials_loaded_from_secure_file': credentials is not None,
        'read_only_auth_probe_http': api_http,
        'catalog_http': catalog_status,
        'catalog_entries': len(catalog_rows),
        'native_http': native_status,
        'native_inventory_authoritative': native_status == 200 and native_rows is not None,
        'native_loaded_entries': len(loaded),
        'expected_model_exactly_loaded': exact,
        # Never equate successful GET, token validity, model residency or
        # this diagnostic report with an independently approved Task lease.
        'approved_runtime_slots': 'NOT_CHECKED',
        'authorizes_dispatch': False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-source', type=Path, required=True)
    parser.add_argument('--credential-file', type=Path, required=True)
    parser.add_argument('--assistx-url', required=True)
    parser.add_argument('--model-url', required=True)
    parser.add_argument('--expected-model', default='')
    parser.add_argument('--expected-sha256', default='')
    parser.add_argument('--service', default='fleet-node-agent.service')
    args = parser.parse_args()
    result = observation(
        worker_source=args.worker_source, credential_file=args.credential_file,
        assistx_url=args.assistx_url, model_url=args.model_url,
        expected_model=args.expected_model, expected_sha256=args.expected_sha256,
        service=args.service)
    print(json.dumps(result, sort_keys=True))
    # A report-only tool must never be treated as an admission/dispatch gate.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())