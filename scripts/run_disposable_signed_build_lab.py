"""Physical disposable signed OCI source→manifest→running-container lab.

This intentionally uses an UNTRUSTED EPHEMERAL test signer on Destroyer,
NOT the production builder/signer. All outcomes HOLD. Only image/container
created by this script can be removed; no global Docker prune or changes to
running API/worker/router/Neo4j.

Requires local Docker buildx, GCC, two pinned source blobs, and SSH to
Destroyer; never provisions signer material or uses a production secret.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__:
    from .verify_disposable_oci_layout import ArtifactHold, inspect_oci_file, git_blob_sha
    from .verify_signed_image_provenance import (
        PREDICATE_TYPE, REPOS, STATEMENT_TYPE, ProvenanceHold, verify,
    )
else:
    from verify_disposable_oci_layout import ArtifactHold, inspect_oci_file, git_blob_sha
    from verify_signed_image_provenance import (
        PREDICATE_TYPE, REPOS, STATEMENT_TYPE, ProvenanceHold, verify,
    )

EXPECTED_GIT_SHA = "edcc142af74695a71ffa3316fb7711ed1bcc9fbb"
EXPECTED_SOURCE_BLOB = "dad70bbde2f51f595545e6ccb0ff41892d8681be"
SERVICE = "assistx-api"
BUILDER = "https://example.test/ephemeral-untrusted-destroyer-signer"
BUILD_TYPE = "https://example.test/disposable-build-from-scratch-v1"
CONTAINER = "assistx-signed-build-stage-20261017"
SOURCE = Path(__file__).resolve().with_name("verify_signed_image_provenance.py")
IMAGE_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
CONTAINER_RE = re.compile(r"[0-9a-f]{64}\Z")

REMOTE_SIGNER = r"""
import base64, hashlib, json, sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
payload = sys.stdin.buffer.read(65537)
if not (0 < len(payload) <= 65536):
    raise ValueError('invalid bounded payload')
key = Ed25519PrivateKey.generate()
pub = key.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
typ = b'application/vnd.in-toto+json'
pae = b'DSSEv1 ' + str(len(typ)).encode() + b' ' + typ + b' ' + str(len(payload)).encode() + b' ' + payload
out = {
    'envelope': {
        'payloadType': typ.decode(),
        'payload': base64.b64encode(payload).decode(),
        'signatures': [{
            'keyid': hashlib.sha256(pub).hexdigest(),
            'sig': base64.b64encode(key.sign(pae)).decode(),
        }],
    },
    'ephemeral_public_key': base64.b64encode(pub).decode(),
}
sys.stdout.write(json.dumps(out, sort_keys=True))
"""


class LabHold(RuntimeError):
    """Unexpected stage identity, unsafe Docker action or failed proof."""


def _cmd(*args: str, input: bytes | None = None, timeout: int = 35) -> str:
    try:
        p = subprocess.run(
            list(args), input=input, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LabHold("disposable subprocess unavailable") from exc
    # No untrusted stderr/stdout on error.
    if p.returncode or len(p.stdout) > 640_000:
        raise LabHold("disposable subprocess returned error")
    return p.stdout.decode("utf-8").strip()


def _docker_local() -> str:
    keys = ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
            "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
    if any(os.environ.get(k) for k in keys):
        raise LabHold("Docker remote override denied")
    if _cmd("docker", "context", "show") != "default":
        raise LabHold("Docker default context required")
    endpoint = _cmd(
        "docker", "context", "inspect", "default",
        "--format", "{{.Endpoints.docker.Host}}",
    )
    if endpoint != "unix:///var/run/docker.sock":
        raise LabHold("Docker socket is not approved local endpoint")
    daemon = _cmd("docker", "info", "--format", "{{.ID}}")
    if not re.fullmatch(r"[A-Za-z0-9-]{24,80}", daemon):
        raise LabHold("invalid Docker daemon identity")
    return daemon


def _inspect(kind: str, identity: str) -> dict[str, Any] | None:
    if kind not in ("container", "image"):
        raise LabHold("unsafe Docker inspect kind")
    try:
        p = subprocess.run(
            ["docker", kind, "inspect", identity],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LabHold("Docker inspect unavailable") from exc
    if p.returncode:
        return None
    try:
        values = json.loads(p.stdout)
    except (ValueError, TypeError) as exc:
        raise LabHold("unparseable Docker inspect response") from exc
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise LabHold("ambiguous Docker inspect response")
    return values[0]


def _statement(digest: str) -> bytes:
    body = {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": "service:" + SERVICE, "digest": {"sha256": digest}}],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": BUILD_TYPE,
                "externalParameters": {
                    "sourceRepository": REPOS[SERVICE],
                    "sourceRevision": EXPECTED_GIT_SHA,
                    "service": SERVICE,
                },
                "resolvedDependencies": [{
                    "uri": "git+" + REPOS[SERVICE],
                    "digest": {"gitCommit": EXPECTED_GIT_SHA},
                }],
            },
            "runDetails": {"builder": {"id": BUILDER}},
        },
    }
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _destroyer_sign(payload: bytes) -> tuple[dict[str, Any], bytes]:
    # This is a disposable remotely generated key with NO trusted signer
    # custody or publicly reviewed trust root. It is not an attestation
    # from the actual build executor on x1-370.
    result = _cmd(
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=yes",
        "destroyer", "python3 -c " + shlex.quote(REMOTE_SIGNER),
        input=payload, timeout=19,
    )
    try:
        doc = json.loads(result)
        if not isinstance(doc, dict) or set(doc) != {
            "envelope", "ephemeral_public_key",
        }:
            raise LabHold("unexpected disposable signer output")
        pub = base64.b64decode(doc["ephemeral_public_key"], validate=True)
        if len(pub) != 32:
            raise LabHold("invalid remote ephemeral signing public key")
        return doc["envelope"], pub
    except (ValueError, TypeError, KeyError) as exc:
        raise LabHold("invalid isolated remote signer response") from exc


def _real_runtime(container_id: str, manifest_id: str) -> dict[str, Any]:
    c = _inspect("container", container_id)
    img = _inspect("image", manifest_id)
    if not c or not img:
        raise LabHold("disposable container/image identity disappeared")
    if (c.get("Id") != container_id or c.get("Name") != "/" + CONTAINER
            or c.get("Image") != manifest_id or img.get("Id") != manifest_id):
        raise LabHold("disposable running image does not bind to OCI manifest")
    if ((c.get("State") or {}).get("Status") != "running"
            or (c.get("HostConfig") or {}).get("NetworkMode") != "none"
            or (c.get("HostConfig") or {}).get("ReadonlyRootfs") is not True
            or (c.get("HostConfig") or {}).get("CapDrop") != ["ALL"]
            or (c.get("Mounts") or [])
            or ((c.get("HostConfig") or {}).get("SecurityOpt") or []) != [
                "no-new-privileges"
            ]):
        raise LabHold("disposable runtime isolation or process state violated")
    return {
        "name": SERVICE,
        "container_id": container_id,
        "state": "running",
        "image": {
            # This comes from verified local OCI archive + loaded image ID.
            # It is NOT supplied by registry RepoDigests, which are absent.
            "local_image_id": manifest_id,
            "manifest_digest_values": [manifest_id],
        },
    }


def run() -> dict[str, Any]:
    daemon = _docker_local()
    if _inspect("container", CONTAINER) is not None:
        raise LabHold("disposable stage container name already occupied")
    if git_blob_sha(SOURCE.read_bytes()) != EXPECTED_SOURCE_BLOB:
        raise LabHold("source is not the exact published Git blob")
    container_id: str | None = None
    image_id: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-signed-build-") as temp:
            work = Path(temp)
            (work / "source").mkdir()
            shutil.copyfile(SOURCE, work / "source" / "verify.py")
            (work / "main.c").write_text(
                "#include <unistd.h>\nint main(void) { for (;;) pause(); return 0; }\n",
                encoding="utf-8",
            )
            _cmd("gcc", "-Os", "-static", "-s", "-o", str(work / "probe"),
                 str(work / "main.c"), timeout=22)
            (work / "Dockerfile").write_text(
                "FROM scratch\nCOPY probe /probe\n"
                "COPY source/verify.py /source/verify.py\n"
                "USER 65534:65534\nENTRYPOINT [\"/probe\"]\n",
                encoding="utf-8",
            )
            image_tar = str(work / "image.oci.tar")
            _cmd(
                "docker", "buildx", "build", "--network", "none",
                "--pull=false", "--provenance=false", "--sbom=false",
                "--output", "type=oci,dest=" + image_tar,
                temp, timeout=65,
            )
            artifact = inspect_oci_file(
                image_tar, expected_source_blob=EXPECTED_SOURCE_BLOB,
            )
            candidate = "sha256:" + artifact["manifest_sha256"]
            if _inspect("image", candidate) is not None:
                # Do not mark this as owned, or finally would remove the
                # unrelated pre-existing image if the build aborts here.
                raise LabHold("source-specific OCI image identity preexisted")
            loaded = _cmd("docker", "load", "-i", image_tar, timeout=38)
            if loaded != "Loaded image ID: " + candidate:
                raise LabHold("Docker OCI load did not return expected manifest")
            image_id = candidate
            container_id = _cmd(
                "docker", "run", "--detach", "--name", CONTAINER,
                "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "65534:65534", "--pids-limit", "16",
                "--memory", "64m", "--label", "assistx.disposable=true",
                image_id, timeout=26,
            )
            if not CONTAINER_RE.fullmatch(container_id):
                raise LabHold("disposable Docker run returned invalid container ID")
            runtime = _real_runtime(container_id, image_id)
            payload = _statement(artifact["manifest_sha256"])
            envelope, remote_key = _destroyer_sign(payload)
            result = verify(
                envelope=envelope, public_key=remote_key,
                expected_key_sha256=hashlib.sha256(remote_key).hexdigest(),
                expected_service=SERVICE,
                expected_git_sha=EXPECTED_GIT_SHA,
                expected_builder_id=BUILDER, expected_build_type=BUILD_TYPE,
                expected_manifest_sha256=artifact["manifest_sha256"],
                runtime_record=runtime,
            )
            if (result["production_decision"] != "HOLD"
                    or result["promotion_authorized"] is not False
                    or result["signature_valid_under_supplied_key"] is not True):
                raise LabHold("disposable signature verification had unsafe decision")

            # Mutated signed subject under original signature MUST deny.
            altered = json.loads(json.dumps(envelope))
            altered["payload"] = base64.b64encode(
                _statement("e" * 64)
            ).decode("ascii")
            try:
                verify(
                    envelope=altered, public_key=remote_key,
                    expected_key_sha256=hashlib.sha256(remote_key).hexdigest(),
                    expected_service=SERVICE, expected_git_sha=EXPECTED_GIT_SHA,
                    expected_builder_id=BUILDER, expected_build_type=BUILD_TYPE,
                    expected_manifest_sha256=artifact["manifest_sha256"],
                    runtime_record=runtime,
                )
            except ProvenanceHold:
                pass
            else:
                raise LabHold("tampered signed OCI statement was accepted")

            # Substituted live image digest must be denied, even with
            # original genuine signature unchanged.
            wrong_runtime = json.loads(json.dumps(runtime))
            wrong_runtime["image"]["manifest_digest_values"] = [
                "sha256:" + "f" * 64
            ]
            try:
                verify(
                    envelope=envelope, public_key=remote_key,
                    expected_key_sha256=hashlib.sha256(remote_key).hexdigest(),
                    expected_service=SERVICE, expected_git_sha=EXPECTED_GIT_SHA,
                    expected_builder_id=BUILDER, expected_build_type=BUILD_TYPE,
                    expected_manifest_sha256=artifact["manifest_sha256"],
                    runtime_record=wrong_runtime,
                )
            except ProvenanceHold:
                pass
            else:
                raise LabHold("swapped runtime image was accepted")
            if _docker_local() != daemon:
                raise LabHold("local Docker daemon changed during disposable build")
            return {
                "production_decision": "HOLD",
                "promotion_authorized": False,
                "evidence_tier": "TWO_NODE_DISPOSABLE_PHYSICAL_SOURCE_OCI_RUNTIME",
                "builder_host": "x1-370",
                "ephemeral_signer_host": "destroyer",
                "signature_valid_under_ephemeral_test_root": True,
                "operator_trusted_independent_signer_proven": False,
                "runtime_manifest_matches_signed_subject": True,
                "source_git_blob_matches_oci_layer": True,
                "mutated_signature_denied": True,
                "substituted_image_denied": True,
                "network_mode": "none",
                "immutable_image_manifest_sha256": artifact["manifest_sha256"],
                "image_layer_count": artifact["layer_count"],
                "live_production_services_modified": False,
            }
    finally:
        # Identity-checked exact disposable cleanup only.
        if container_id:
            existing = _inspect("container", CONTAINER)
            if existing is None or existing.get("Id") != container_id:
                raise LabHold("cleanup refused: disposable container identity changed")
            _cmd("docker", "rm", "--force", container_id, timeout=25)
        if image_id:
            current = _inspect("image", image_id)
            if current is not None:
                if current.get("Id") != image_id:
                    raise LabHold("cleanup refused: disposable image identity drift")
                if current.get("RepoTags") or current.get("RepoDigests"):
                    raise LabHold("cleanup refused: disposable image has foreign tags")
                _cmd("docker", "image", "rm", image_id, timeout=25)


def main() -> int:
    try:
        result = run()
    except (LabHold, ArtifactHold, ProvenanceHold, ValueError, OSError) as exc:
        # Do not relay subprocess command stderr or secret-bearing output.
        print(json.dumps({
            "production_decision": "HOLD", "promotion_authorized": False,
            "experiment": "NO_GO", "reason": type(exc).__name__,
        }, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
