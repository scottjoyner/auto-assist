"""Full assembled FastAPI route isolation; all credentials and state are synthetic.

Runs in an independent subprocess so API import-time authentication settings do
not contaminate other tests. The subprocess denies outbound socket connections
and redirects any import-time local files to a temporary directory.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_CHILD = r"""
import os
import socket

attempts = []

def deny_network(sock, address):
    attempts.append(str(address)[:80])
    raise AssertionError('network access forbidden in auth integration fixture')

socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network

from fastapi.testclient import TestClient
from assistx.api_router import app
from assistx import api, scoped_machine_poll

# The assembled API overrides any environment-provided trusted identity
# header with its own executor-only header; external copies are stripped.
assert api.TRUSTED_AUTH_HEADER.lower() == 'x-assistx-executor-identity'

expected = {
    ('GET', '/api/agent/tasks'),
    ('POST', '/api/tasks/{task_id}/claim'),
    ('POST', '/api/tasks/{task_id}/heartbeat'),
    ('POST', '/api/tasks/{task_id}/complete'),
    ('GET', '/api/fleet/nodes/{node_id}/assigned-task-hints'),
}
actual = {(m, r.path) for r in app.routes
          for m in (getattr(r, 'methods', None) or []) if hasattr(r, 'path')}
assert expected.issubset(actual), sorted(expected - actual)

class NoGraph:
    def list_agent_tasks(self, **kwargs):
        assert kwargs['status'] == 'READY'
        return []

# Only a synthetic operator with valid Basic auth may invoke the stubbed
# legacy handler; unauthorized callers must be denied before any graph query.
api._neo_fleet = lambda: NoGraph()
api._workflow_runtime_snapshot = lambda n: {'running': 0, 'batch_ready_direct': 0}
api._apply_workflow_admission = lambda items, runtime: items
api._get_workflow_control = lambda: {'control': 'synthetic'}

client = TestClient(app, raise_server_exceptions=False)
node_headers = {'X-Fleet-Node-Token': 'synthetic-node-token-x'}
cases = [
    ('GET', '/api/agent/tasks'),
    ('POST', '/api/tasks/synthetic/claim'),
    ('POST', '/api/tasks/synthetic/heartbeat'),
    ('POST', '/api/tasks/synthetic/complete'),
]
for method, path in cases:
    got = client.request(method, path, headers=node_headers,
                         json={} if method == 'POST' else None)
    assert got.status_code == 401, (method, path, got.status_code)

# Neither the configured trusted-header name nor the internal executor identity
# may be accepted as a client-provided operator credential. This exercises the
# full installed middleware, not a standalone auth dependency.
for headers in (
    {'X-Synthetic-Trusted-Identity': 'operator'},
    {'X-Assistx-Executor-Identity': 'executor-bootstrap'},
    {'X-Assistx-Executor-Identity': 'operator',
     'X-Synthetic-Trusted-Identity': 'operator'},
):
    denied = client.get('/api/agent/tasks', headers=headers)
    assert denied.status_code == 401, (sorted(headers), denied.status_code)

# High-impact fleet administration routes must also reject machine tokens
# before any loader, model or control-plane effects can occur.
for method, path in (
    ('GET', '/api/fleet/models'),
    ('POST', '/api/fleet/loader/load'),
    ('POST', '/api/fleet/refresh-models'),
):
    denied = client.request(method, path, headers=node_headers,
                            json={} if method == 'POST' else None)
    assert denied.status_code == 401, (method, path, denied.status_code)

# Operator Basic must remain authenticated on existing listing route.
operator = ('synthetic-operator', 'synthetic-pass-for-testing')
authorized = client.get('/api/agent/tasks', auth=operator)
assert authorized.status_code == 200, authorized.status_code
assert authorized.json()['items'] == []

# But Basic authentication alone never substitutes for a machine token.
path = '/api/fleet/nodes/xwing/assigned-task-hints'
assert client.get(path, auth=operator).status_code == 403
assert client.get(path, headers={'X-Fleet-Node-Token': 'wrong'}).status_code == 403
assert client.get(path, headers={'X-Fleet-Node-Token': 'synthetic-node-token-other'}).status_code == 403
assert client.get('/api/fleet/nodes/other/assigned-task-hints', headers=node_headers).status_code == 403

# Disable must fail with 404 and cannot access runtime/graph.
os.environ['ASSISTX_ENABLE_SCOPED_NODE_OBSERVATION'] = '0'
assert client.get(path, headers=node_headers).status_code == 404
os.environ['ASSISTX_ENABLE_SCOPED_NODE_OBSERVATION'] = '1'

# Registration alone without explicit per-node policy is insufficient.
assert client.get(path, headers=node_headers).status_code == 403

# An enrolled token alone cannot grant hints without a pinned token epoch.
import json
os.environ['ASSISTX_SCOPED_NODE_OBSERVATION_POLICIES'] = json.dumps({
 'xwing': {'enabled': True, 'audience': 'targeted-llm-observer',
           'allowed_capabilities': ['llm'], 'projection_generation': 17,
           'artifact_fingerprint': 'a' * 64}
})
assert client.get(path, headers=node_headers).status_code == 403

# With an explicit current synthetic epoch, unavailable runtime is still HOLD.
os.environ['ASSISTX_SCOPED_NODE_OBSERVATION_POLICIES'] = json.dumps({
 'xwing': {'enabled': True, 'audience': 'targeted-llm-observer',
           'allowed_capabilities': ['llm'], 'projection_generation': 17,
           'artifact_fingerprint': 'a' * 64, 'token_epoch': 3}
})
def denied_projection(*args):
    raise scoped_machine_poll.RuntimeProjectionBlocked('synthetic no admission')
scoped_machine_poll._projection_state = denied_projection
hold = client.get(path, headers=node_headers)
assert hold.status_code == 503, hold.status_code
assert not attempts, attempts
print('FULL_ASSEMBLED_API_ISOLATION_PASS: 4 legacy denies, '
      '3 forged-header denies, 3 admin denies, operator allowed, '
      'observer Basic/cross-node/policy/feature/authority denies; no network')
"""


def test_machine_operator_routes_isolated_in_complete_api():
    with tempfile.TemporaryDirectory(prefix="assistx-machine-route-test-") as tmp:
        env = {
            key: value for key, value in os.environ.items()
            if not (key.startswith(("NEO4J_", "REDIS_", "ASSISTX_"))
                    or key in {"BASIC_AUTH_USER", "BASIC_AUTH_PASS"})
        }
        root = Path(tmp)
        env.update({
            "BASIC_AUTH_USER": "synthetic-operator",
            "BASIC_AUTH_PASS": "synthetic-pass-for-testing",
            "TRUSTED_AUTH_HEADER": "X-Synthetic-Trusted-Identity",
            "ASSISTX_USER": "synthetic-operator",
            "ASSISTX_PASS": "synthetic-pass-for-testing",
            "ASSISTX_ENABLE_SCOPED_NODE_OBSERVATION": "1",
            "ASSISTX_FLEET_NODE_TOKENS": (
                '{"xwing":"synthetic-node-token-x",'
                '"other":"synthetic-node-token-other"}'
            ),
            "ASSISTX_SCOPED_NODE_OBSERVATION_POLICIES": "{}",
            "ASSISTX_OUTBOX_DB": str(root / "outbox.sqlite"),
            "TRANSCRIPTIONS_ROOT": str(root / "transcriptions"),
            "CAPTURES_ROOT": str(root / "captures"),
            "NEO4J_URI": "bolt://127.0.0.1:1",
            "NEO4J_USER": "synthetic",
            "NEO4J_PASSWORD": "synthetic",
            "REDIS_URL": "redis://127.0.0.1:1/0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        })
        result = subprocess.run(
            [sys.executable, "-c", _CHILD],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=35,
            check=False,
        )
        assert result.returncode == 0, (
            f"full app security isolation failed: {result.stderr[-2500:]}"
        )
        assert "FULL_ASSEMBLED_API_ISOLATION_PASS" in result.stdout


if __name__ == "__main__":
    test_machine_operator_routes_isolated_in_complete_api()
    print("PASS")
