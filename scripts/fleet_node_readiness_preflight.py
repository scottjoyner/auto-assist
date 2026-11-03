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
import hmac
import ipaddress
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request


def _credential_target_allowed(url: str) -> bool:
    """Restrict Basic auth to explicit local/overlay nodes; fail closed on DNS.

    HTTPS permits RFC1918 addresses. HTTP permits only loopback or Tailnet
    CGNAT addresses; link-local cloud metadata/public destinations are denied.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or parsed.port is None
                or parsed.path != '/api/agent/tasks'):
            return False
        if parsed.hostname == 'localhost':
            return True
        ip = ipaddress.ip_address(parsed.hostname)
        if isinstance(ip, ipaddress.IPv4Address):
            allowed_http = ('127.0.0.0/8', '100.64.0.0/10')
            allowed_https = allowed_http + ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
        else:
            allowed_http = ('::1/128',)
            allowed_https = allowed_http + ('fc00::/7',)
        blocks = allowed_http if parsed.scheme == 'http' else allowed_https
        return any(ip in ipaddress.ip_network(block) for block in blocks)
    except ValueError:
        return False


def _running_credentials(service: str) -> tuple[str, str] | None:
    """Read active same-user worker credentials, never return them in a report."""
    try:
        result = subprocess.run(
            ['systemctl', '--user', 'show', service, '--property=MainPID', '--value'],
            capture_output=True, text=True, timeout=3, check=False)
        if result.returncode != 0:
            return None
        pid = int(result.stdout.strip())
        if pid < 2:
            return None
        proc = Path(f'/proc/{pid}')
        if proc.stat().st_uid != os.geteuid():
            return None
        command = (proc / 'cmdline').read_bytes()[:8192]
        if b'fleet_node_agent' not in command and b'run_fleet_node_agent' not in command:
            return None
        data = (proc / 'environ').read_bytes()
        if len(data) > 65536:
            return None
        found: dict[str, str] = {}
        for token in data.split(b'\0'):
            if b'=' not in token:
                continue
            key, value = token.split(b'=', 1)
            if key not in (b'FLEET_AUTH_USER', b'FLEET_AUTH_PASS'):
                continue
            name = key.decode('ascii')
            if name in found:
                return None
            found[name] = value.decode('utf-8')
        if found.get('FLEET_AUTH_USER') and found.get('FLEET_AUTH_PASS'):
            return found['FLEET_AUTH_USER'], found['FLEET_AUTH_PASS']
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        pass
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _get(url: str, *, credentials: tuple[str, str] | None = None) -> tuple[int, dict]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        return 0, {}
    if credentials is not None and not _credential_target_allowed(url):
        return 0, {}
    headers = {'Accept': 'application/json'}
    if credentials is not None:
        raw = (credentials[0] + ':' + credentials[1]).encode()
        headers['Authorization'] = 'Basic ' + base64.b64encode(raw).decode('ascii')
    req = urllib.request.Request(url, headers=headers, method='GET')
    # Never forward existing per-node Basic credentials through an inherited
    # HTTP(S)_PROXY environment setting. Tailnet service must be direct.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        with opener.open(req, timeout=4) as response:
            if response.status != 200:
                return response.status, {}
            try:
                raw = response.read(131073)
                if len(raw) > 131072:
                    return 0, {}
                obj = json.loads(raw)
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
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, 'r', encoding='utf-8') as fh:
            st = os.fstat(fh.fileno())
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                    or st.st_mode & 0o077 or st.st_size > 16384):
                return None
            content = fh.read(16385)
            if len(content) > 16384:
                return None
        pairs = {}
        for line in content.splitlines():
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
    running = _service_active(service)
    effective = _running_credentials(service) if running else None
    matches_live: bool | str = 'NOT_CHECKED'
    if credentials is not None and effective is not None:
        matches_live = (hmac.compare_digest(credentials[0], effective[0])
                        and hmac.compare_digest(credentials[1], effective[1]))
    task_endpoint = assistx_url.rstrip('/') + '/api/agent/tasks?status=READY&limit=1'
    api_http = None
    if credentials is not None:
        api_http, _ = _get(task_endpoint, credentials=credentials)
    effective_api_http = None
    if effective is not None:
        if matches_live is True and api_http is not None:
            effective_api_http = api_http
        else:
            effective_api_http, _ = _get(task_endpoint, credentials=effective)
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
    # A resident embedding model is useful for retrieval, but cannot be
    # advertised as a chat-generation lane. Preserve generic observations for
    # compatibility and publish explicit capability counts separately.
    chat = [row for row in loaded if row.get('type') == 'llm']
    embeddings = [row for row in loaded if row.get('type') == 'embedding']
    exact = any(row['key'] == expected_model for row in chat) if expected_model else None
    return {
        'schema_version': 'fleet-agent-readonly-preflight.v1',
        'service_active': running,
        'worker_source_present': bool(source),
        'source_sha256': source_hash,
        'source_matches_expected': source_hash == expected_sha256 if expected_sha256 else None,
        'old_state_ready_query': 'state=READY&limit=20' in source,
        'backoff_implemented': 'auth_denials' in source,
        'credentials_loaded_from_secure_file': credentials is not None,
        'read_only_auth_probe_http': api_http,
        # Never emit username, password, or digest of a weak credential.
        'matches_live_process_credentials': matches_live,
        'live_process_credentials_present': effective is not None,
        'live_process_read_only_auth_probe_http': effective_api_http,
        'authentication_grants_task_admission': False,
        'catalog_http': catalog_status,
        'catalog_entries': len(catalog_rows),
        'native_http': native_status,
        'native_inventory_authoritative': native_status == 200 and native_rows is not None,
        'native_loaded_entries': len(loaded),
        'native_loaded_chat_entries': len(chat),
        'native_loaded_embedding_entries': len(embeddings),
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
