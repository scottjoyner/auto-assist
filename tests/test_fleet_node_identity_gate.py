"""Synthetic no-worker assertions for the source-pinned one-shot launcher."""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("fleet_node_agent.py", "lm_studio_client_auth.py", "recovery_runbooks.py")


@pytest.fixture
def stage(tmp_path):
    directory = tmp_path / "pinned-stage"
    directory.mkdir(mode=0o700)
    package = directory / "src" / "assistx"
    package.mkdir(parents=True)
    (directory / "src").chmod(0o700)
    package.chmod(0o700)
    (package / "__init__.py").touch()
    (package / "__init__.py").chmod(0o600)
    for name in MODULES:
        shutil.copy2(ROOT / "src" / "assistx" / name, package / name)
        (package / name).chmod(0o600)
    shutil.copy2(ROOT / "scripts" / "one_shot_identity_gate.py", directory)
    return directory


def _invoke(stage, *, identity_only=False, extra_env=None):
    env = {"HOME": str(stage.parent), "PATH": os.defpath,
           "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(extra_env or {})
    args = ["python3", str(stage / "one_shot_identity_gate.py")]
    if identity_only:
        args.append("--identity-only")
    result = subprocess.run(args, env=env, capture_output=True, text=True,
                            check=False, timeout=9)
    return result.returncode, json.loads(result.stdout)


def test_default_mode_pins_source_and_never_admits(stage):
    rc, output = _invoke(stage)
    assert rc == 2
    assert output == {"contract": "fleet-staging-gate-v1", "source_verified": True,
                      "identity_verified": False, "task_admission": "HOLD",
                      "production_admission_permitted": False,
                      "reason": "SOURCE_PINNED_NO_ADMISSION"}


def test_identity_mode_without_owner_credential_holds(stage):
    rc, output = _invoke(stage, identity_only=True)
    assert rc == 2
    assert output["reason"] == "NODE_IDENTITY_NOT_PROVISIONED"
    assert output["task_admission"] == "HOLD"


def test_tampered_stage_file_blocks_before_identity(stage):
    (stage / "src/assistx/fleet_node_agent.py").write_bytes(b"tampered\n")
    rc, output = _invoke(stage, identity_only=True, extra_env={
        "FLEET_NODE_ID": "synthetic-staging", "FLEET_NODE_TOKEN": "synthetic-token",
        "FLEET_NODE_IDENTITY_APPROVED_ORIGIN": "http://127.0.0.1:1"})
    assert rc == 2
    assert output["reason"] == "SOURCE_HASH_OR_CUSTODY_MISMATCH"
    assert output["source_verified"] is False


def test_extra_package_init_code_is_refused(stage):
    (stage / "src/assistx/__init__.py").write_text("print('unexpected')\n")
    rc, output = _invoke(stage)
    assert rc == 2 and output["source_verified"] is False
    assert output["reason"] == "SOURCE_HASH_OR_CUSTODY_MISMATCH"


def test_candidate_prohibits_bytecode_sidecars(stage):
    (stage / "src/assistx/__pycache__").mkdir()
    rc, output = _invoke(stage)
    assert rc == 2 and output["source_verified"] is False


def test_unapproved_nonloopback_http_origin_fails_closed(stage):
    rc, output = _invoke(stage, identity_only=True, extra_env={
        "FLEET_NODE_ID": "synthetic-staging", "FLEET_NODE_TOKEN": "synthetic-token",
        "FLEET_NODE_IDENTITY_APPROVED_ORIGIN": "http://example.invalid"})
    assert rc == 2 and output["reason"] == "ORIGIN_NOT_APPROVED"
    assert output["identity_verified"] is False


def test_owner_pinned_synthetic_loopback_succeeds_without_task_authority(stage):
    node = "synthetic-staging"
    token = "fake-token-never-a-live-node-credential"
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            seen.append((self.path, self.headers.get("X-Fleet-Node-Id"),
                         self.headers.get("X-Fleet-Node-Token"),
                         self.headers.get("Authorization")))
            payload = json.dumps({"machine_identity_verified": True, "node_id": node,
                                  "task_admission": "HOLD",
                                  "legacy_task_authorized": False}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 8
    origin = f"http://127.0.0.1:{server.server_port}"
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    try:
        rc, output = _invoke(stage, identity_only=True, extra_env={
            "FLEET_NODE_ID": node, "FLEET_NODE_TOKEN": token,
            "FLEET_NODE_IDENTITY_APPROVED_ORIGIN": origin,
            "FLEET_AUTH_USER": "do-not-forward", "FLEET_AUTH_PASS": "do-not-forward"})
    finally:
        server.server_close()
    worker.join(timeout=2)
    assert rc == 2
    assert output["identity_verified"] is True
    assert output["task_admission"] == "HOLD"
    assert output["production_admission_permitted"] is False
    assert output["reason"] == "IDENTITY_ONLY_NO_TASK_AUTHORITY"
    assert seen == [("/api/fleet-node/identity", node, token, None)]
