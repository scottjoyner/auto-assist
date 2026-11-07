"""Synthetic loopback checks of the exact pinned, uninstalled node candidate.

No operator credentials, Task API, running worker or production host is used.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import threading


PINNED_FILES = {
    "fleet_node_agent.py": "02d7ea9f9ca637376a810ad0f537e8cf2b0e200090a7e506bbb215eafe5057b0",
    "lm_studio_client_auth.py": "12a1a444948b0c115a455f2664d88b2feea09977ea75b20af1d795a213c29467",
    "recovery_runbooks.py": "c946869ab6ba3c9907879e7bbb15a5f5d77abe2a331ed1b915c640e8e4623859",
}


def _staged_package(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "src" / "assistx"
    package = tmp_path / "src" / "assistx"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    for filename, expected in PINNED_FILES.items():
        data = (source / filename).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, filename
        (package / filename).write_bytes(data)
    return package.parent


def _env(tmp_path: Path, source_root: Path, *, token: str = "") -> dict[str, str]:
    return {"PATH": os.defpath, "HOME": str(tmp_path),
            "PYTHONPATH": str(source_root), "PYTHONDONTWRITEBYTECODE": "1",
            "FLEET_NODE_ID": "synthetic-staging-node", "FLEET_NODE_TOKEN": token}


def _invoke(source_root: Path, env: dict[str, str], url: str,
            *, identity_only: bool) -> subprocess.CompletedProcess[str]:
    args = ["python3", "-m", "assistx.fleet_node_agent"]
    if identity_only:
        args.append("--identity-probe-only")
    args.extend(["--assistx-url", url, "--router-url", url])
    return subprocess.run(args, env=env, capture_output=True, text=True,
                          check=False, timeout=8)


def test_approved_synthetic_identity_get_never_admits_task(tmp_path):
    source_root = _staged_package(tmp_path)
    node = "synthetic-staging-node"
    token = "synthetic-not-a-real-credential"
    seen: list[tuple[str, str | None, str | None, str | None]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("X-Fleet-Node-Id"),
                         self.headers.get("X-Fleet-Node-Token"),
                         self.headers.get("Authorization")))
            if self.path != "/api/fleet-node/identity":
                self.send_error(403)
                return
            data = json.dumps({"machine_identity_verified": True, "node_id": node,
                               "task_admission": "HOLD",
                               "legacy_task_authorized": False}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 7
    url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    env = _env(tmp_path, source_root, token=token)
    env["FLEET_NODE_IDENTITY_APPROVED_ORIGIN"] = url
    try:
        run = _invoke(source_root, env, url, identity_only=True)
    finally:
        server.server_close()
    thread.join(timeout=2)
    assert run.returncode == 2, run.stderr[:160]
    assert json.loads(run.stdout) == {
        "contract": "fleet-node-identity-probe-v1", "identity_verified": True,
        "legacy_task_authorized": False, "production_admission_permitted": False,
        "reason": "IDENTITY_ONLY_NO_TASK_AUTHORITY", "task_admission": "HOLD",
    }
    assert seen == [("/api/fleet-node/identity", node, token, None)]


def test_missing_node_token_holds_without_identity_request(tmp_path):
    source_root = _staged_package(tmp_path)
    env = _env(tmp_path, source_root)
    run = _invoke(source_root, env, "http://127.0.0.1:1", identity_only=True)
    assert run.returncode == 2
    response = json.loads(run.stdout)
    assert response["identity_verified"] is False
    assert response["task_admission"] == "HOLD"
    assert response["reason"] == "MISSING_OR_MISMATCHED_NODE_IDENTITY"


def test_machine_identity_cannot_start_legacy_worker(tmp_path):
    source_root = _staged_package(tmp_path)
    env = _env(tmp_path, source_root, token="synthetic-not-a-real-credential")
    run = _invoke(source_root, env, "http://127.0.0.1:1", identity_only=False)
    assert run.returncode != 0
    assert "machine identity mode has no legacy Task authority" in run.stderr
