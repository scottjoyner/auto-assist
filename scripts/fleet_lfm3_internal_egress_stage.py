#!/usr/bin/env python3
"""One-shot egress-denied LFM3 model+gateway+client staging proof.

Never provisions real approval or Task admission. By default, no effects. A
manual --live run creates a distinct disposable Docker `--internal` network
and three unprivileged containers, then destroys ALL staging resources.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from fleet_lfm3_namespaced_operator_probe import (
    CLIENT,
    MODEL_BINARY,
    MODEL_FILE,
    MODEL_LIBS,
    MODEL_NAME,
    MODEL_PORT,
    REPO,
    docker,
    safe_ip,
    valid_no_direct_model,
)
from fleet_resource_slot_local_staging_witness import provision_disposable_synthetic_db

NETWORK = "lfm3-internal-egress-stage-20261108"
MODEL_CONTAINER = "lfm3-internal-stage-model-20261108"
GATEWAY_CONTAINER = "lfm3-internal-stage-gateway-20261108"
CLIENT_CONTAINER = "lfm3-internal-stage-client-20261108"
IMAGE = "sha256:74df40c5c720dbef283f83eaf69eae585212748ca3889be508eaa9c88cd5b4c4"
GGUF_SHA256 = "3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4"
OWNED_ID = re.compile(r"[0-9a-f]{64}\\Z")
EGRESS = """
import socket
for dest in [('1.1.1.1',443),('8.8.8.8',53)]:
    with socket.socket() as sock:
        sock.settimeout(0.7)
        try:
            connected = sock.connect_ex(dest) == 0
        except OSError:
            connected = False  # no default route is an expected hard denial
        assert not connected,'external_egress_not_fenced'
print('external_ipv4_tcp_egress=DENIED')
"""


def _attested_local_inputs() -> None:
    if os.geteuid() == 0:
        raise ValueError("nonroot_operator_required")
    if docker("image", "inspect", IMAGE, "--format", "{{.Id}}", timeout=7) != IMAGE:
        raise ValueError("immutable_cached_image_mismatch")
    digest = hashlib.sha256()
    with open(MODEL_FILE, "rb") as model:
        for piece in iter(lambda: model.read(4 * 1024 * 1024), b""):
            digest.update(piece)
    if digest.hexdigest() != GGUF_SHA256:
        raise ValueError("model_bytes_not_canonical")
    existing = docker("ps", "-a", "--format", "{{.Names}}", timeout=8).splitlines()
    if any(name in existing for name in (MODEL_CONTAINER, GATEWAY_CONTAINER, CLIENT_CONTAINER)):
        raise ValueError("operator_stage_name_occupied")
    if NETWORK in docker("network", "ls", "--format", "{{.Name}}", timeout=8).splitlines():
        raise ValueError("stage_network_name_occupied")


def _network_attest(*, expected_count: int, expected_containers: set[str]) -> dict:
    info = json.loads(docker("network", "inspect", NETWORK, "--format", "{{json .}}", timeout=8))
    if (
        not isinstance(info, dict)
        or info.get("Name") != NETWORK
        or info.get("Driver") != "bridge"
        or info.get("Internal") is not True
        or info.get("Ingress") is not False
        or info.get("Attachable") is not False
    ):
        raise ValueError("network_is_not_internal_and_private")
    attached = info.get("Containers") or {}
    names = {v.get("Name") for v in attached.values()}
    if len(attached) != expected_count or names != expected_containers:
        raise ValueError("network_attachment_census_changed")
    return info


def _ip_for(name: str) -> str:
    connections = json.loads(docker("inspect", name, "--format", "{{json .NetworkSettings.Networks}}", timeout=8))
    if not isinstance(connections, dict) or list(connections) != [NETWORK]:
        raise ValueError("unexpected_container_network_attach")
    return safe_ip(connections[NETWORK]["IPAddress"])


def _deny_external_egress(name: str) -> None:
    evidence = docker("exec", name, "python3", "-c", EGRESS, timeout=5)
    if "external_ipv4_tcp_egress=DENIED" not in evidence:
        raise ValueError("network_egress_not_denied")


def _created_id(value: str) -> str:
    # IDs are supplied only by successful Docker creation or our private
    # --cidfile; names are never proof of ownership.
    if not isinstance(value, str) or not OWNED_ID.fullmatch(value):
        raise ValueError("created_docker_identity_unverified")
    return value


def _cleanup(container_ids: list[str], network_id: str | None) -> None:
    """Remove only IDs returned by this run. Fail closed if removal is unproven."""
    if not container_ids and network_id is None:
        return
    for cid in reversed(container_ids):
        _created_id(cid)
    if network_id is not None:
        _created_id(network_id)
    for cid in reversed(container_ids):
        try:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=5)
        except subprocess.SubprocessError:
            pass
    if network_id is not None:
        try:
            subprocess.run(["docker", "network", "rm", network_id], capture_output=True, timeout=7)
        except subprocess.SubprocessError:
            pass
    # A failed 'rm' can be harmless for --rm containers that already exited.
    # Independently require an accessible daemon and absence of every exact ID.
    containers = set(docker("ps", "-a", "--no-trunc", "--format", "{{.ID}}", timeout=7).splitlines())
    if any(cid in containers for cid in container_ids):
        raise ValueError("owned_stage_container_cleanup_failed")
    if network_id is not None:
        networks = set(docker("network", "ls", "--no-trunc", "--format", "{{.ID}}", timeout=7).splitlines())
        if network_id in networks:
            raise ValueError("owned_stage_network_cleanup_failed")


def _docker_run(*params: str, timeout: float = 12.0) -> str:
    return _created_id(docker("run", "--pull", "never", "-d", "--rm", *params, timeout=timeout))


def run() -> dict:
    _attested_local_inputs()
    private = None
    child = None
    gateway_cidfile = None
    owned_container_ids: list[str] = []
    owned_network_id = None
    try:
        owned_network_id = _created_id(
            docker("network", "create", "--internal", "--driver", "bridge", NETWORK, timeout=9)
        )
        _network_attest(expected_count=0, expected_containers=set())
        owned_model_id = _docker_run(
            "--name",
            MODEL_CONTAINER,
            "--network",
            NETWORK,
            "--read-only",
            "--user",
            "1000:1000",
            "--memory",
            "3g",
            "--cpus",
            "3",
            "--pids-limit",
            "96",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=96m",
            "-v",
            MODEL_BINARY + ":/opt/lfm3-runtime:ro",
            "-v",
            MODEL_LIBS + ":" + MODEL_LIBS + ":ro",
            "-v",
            MODEL_FILE + ":/opt/lfm3.gguf:ro",
            "--entrypoint",
            "/opt/lfm3-runtime/bin/llama-server",
            IMAGE,
            "--model",
            "/opt/lfm3.gguf",
            "--alias",
            MODEL_NAME,
            "--host",
            "127.0.0.1",
            "--port",
            str(MODEL_PORT),
            "--threads",
            "3",
            "--parallel",
            "1",
            "--ctx-size",
            "1024",
            "--batch-size",
            "128",
            "--n-gpu-layers",
            "0",
            "--no-webui",
            timeout=14,
        )
        owned_container_ids.append(owned_model_id)
        model_ip = _ip_for(MODEL_CONTAINER)
        _network_attest(expected_count=1, expected_containers={MODEL_CONTAINER})
        exposed = json.loads(docker("inspect", MODEL_CONTAINER, "--format", "{{json .HostConfig.PortBindings}}"))
        if exposed not in ({}, None):
            raise ValueError("unapproved_model_host_port")
        valid_no_direct_model(model_ip)
        _deny_external_egress(MODEL_CONTAINER)
        is_healthy = False
        for _ in range(24):
            try:
                docker(
                    "exec",
                    MODEL_CONTAINER,
                    "python3",
                    "-c",
                    "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:19185/health',timeout=1))['status']=='ok'",
                    timeout=3,
                )
                is_healthy = True
                break
            except (ValueError, subprocess.TimeoutExpired):
                time.sleep(0.6)
        if not is_healthy:
            raise ValueError("internal_model_not_ready")
        valid_no_direct_model(model_ip)
        owned_client_id = _docker_run(
            "--name",
            CLIENT_CONTAINER,
            "--network",
            NETWORK,
            "--read-only",
            "--user",
            "1000:1000",
            "--memory",
            "384m",
            "--cpus",
            "1",
            "--pids-limit",
            "40",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=16m",
            "--entrypoint",
            "python3",
            IMAGE,
            "-c",
            "import time; time.sleep(32)",
        )
        owned_container_ids.append(owned_client_id)
        client_ip = _ip_for(CLIENT_CONTAINER)
        if client_ip == model_ip:
            raise ValueError("model_client_ip_conflict")
        _network_attest(expected_count=2, expected_containers={MODEL_CONTAINER, CLIENT_CONTAINER})
        _deny_external_egress(CLIENT_CONTAINER)
        # Model is private to its own loopback, even for a client sharing the
        # same egress-blocked physical Docker bridge.
        docker(
            "exec",
            CLIENT_CONTAINER,
            "python3",
            "-c",
            "import socket,sys;s=socket.socket();s.settimeout(.8);assert s.connect_ex((sys.argv[1],19185))!=0",
            model_ip,
            timeout=4,
        )
        private = tempfile.TemporaryDirectory(prefix="lfm3-internal-egress-grant-stage-")
        folder = Path(private.name)
        folder.chmod(0o700)
        gateway_cidfile = folder / "gateway.cid"
        db = folder / "synthetic-slot-ledger.sqlite"
        provision_disposable_synthetic_db(db, now_ms=int(time.time() * 1000))
        secret = secrets.token_bytes(32)
        cmd = [
            "docker",
            "run",
            "--pull",
            "never",
            "--rm",
            "-i",
            "--cidfile",
            str(gateway_cidfile),
            "--name",
            GATEWAY_CONTAINER,
            "--network",
            "container:" + MODEL_CONTAINER,
            "--read-only",
            "--user",
            "1000:1000",
            "--memory",
            "1g",
            "--cpus",
            "2",
            "--pids-limit",
            "96",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=48m",
            "-v",
            str(REPO) + ":/work:ro",
            "-v",
            str(folder) + ":/staging:rw",
            "--entrypoint",
            "python3",
            IMAGE,
            "/work/scripts/fleet_lfm3_namespaced_slot_gateway.py",
            "--staging",
            "--bind-ip",
            model_ip,
            "--client-ip",
            client_ip,
            "--ledger-path",
            "/staging/synthetic-slot-ledger.sqlite",
            "--max-seconds",
            "14",
            "--max-requests",
            "3",  # invalid HMAC, accepted HMAC, replay: exact bounded request count
        ]
        child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if child.stdin is None or child.stdout is None:
            raise ValueError("gateway_process_pipes_missing")
        child.stdin.write(secret)
        child.stdin.close()
        import select

        ready, _, _ = select.select([child.stdout], [], [], 10)
        if not ready or json.loads(child.stdout.readline()).get("status") != "ISOLATED_MODEL_SLOT_GATEWAY_READY":
            raise ValueError("internal_gateway_not_ready")
        network_mode = docker("inspect", GATEWAY_CONTAINER, "--format", "{{.HostConfig.NetworkMode}}")
        model_id = docker("inspect", MODEL_CONTAINER, "--format", "{{.Id}}")
        if network_mode not in ("container:" + MODEL_CONTAINER, "container:" + model_id):
            raise ValueError("gateway_not_co_namespaced_to_model")
        _network_attest(expected_count=2, expected_containers={MODEL_CONTAINER, CLIENT_CONTAINER})
        _deny_external_egress(GATEWAY_CONTAINER)
        result = subprocess.run(
            ["docker", "exec", "-i", CLIENT_CONTAINER, "python3", "-c", CLIENT, model_ip],
            input=secret.hex(),
            capture_output=True,
            text=True,
            timeout=13,
        )
        if result.returncode != 0:
            raise ValueError("private_client_to_gateway_failed")
        evidence = json.loads(result.stdout.strip())
        if (
            not evidence.get("bad_hmac_denied")
            or not evidence.get("replay_denied")
            or not evidence.get("model_direct_network_denied")
            or evidence.get("gateway_http") != 200
            or evidence.get("task_admission") != "HOLD"
        ):
            raise ValueError("internal_stage_evidence_invalid")
        if child.wait(timeout=8) != 2 or '"status": "CLOSED"' not in child.stdout.read(400).decode():
            raise ValueError("internal_gateway_did_not_fail_closed")
        valid_no_direct_model(model_ip)
        return {
            "physical_stage": "PASS",
            "network_internal": True,
            "separate_model_and_gateway_shared_namespace": True,
            "network_egress_model_denied": True,
            "network_egress_gateway_denied": True,
            "network_egress_client_denied": True,
            "host_and_client_direct_model_denied": True,
            "image_id_sha256_pinned": True,
            "model_gguf_sha256_pinned": True,
            **evidence,
            "approved_parallel_slots": 0,
            "owner_signed": False,
            "task_admission": "HOLD",
            "not_a_production_grant": True,
        }
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        cidfile_problem = False
        if gateway_cidfile is not None:
            try:
                if gateway_cidfile.is_symlink():
                    raise ValueError("gateway_cidfile_symlink")
                owned_container_ids.append(_created_id(gateway_cidfile.read_text().strip()))
            except FileNotFoundError:
                # Docker may have refused container creation entirely.
                pass
            except (ValueError, OSError):
                cidfile_problem = True
        try:
            _cleanup(owned_container_ids, owned_network_id)
        finally:
            if private is not None:
                private.cleanup()
        if cidfile_problem:
            raise ValueError("gateway_container_identity_unverified")


def _termination_requested(_signum, _frame):
    raise ValueError("operator_stage_termination_requested")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({"status": "HOLD", "reason": "EXPLICIT_INTERNAL_STAGE_ONLY", "task_admission": "HOLD"}))
        return 2
    previous = signal.signal(signal.SIGTERM, _termination_requested)
    try:
        try:
            result = run()
            print(json.dumps(result, sort_keys=True))
        except (ValueError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError):
            print(
                json.dumps(
                    {"physical_stage": "HOLD", "task_admission": "HOLD", "reason": "INTERNAL_EGRESS_PROOF_FAILED"}
                )
            )
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
