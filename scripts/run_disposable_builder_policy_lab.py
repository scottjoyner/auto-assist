"""Three-node EPHEMERAL policy-signature research; synthetic runtime only.

x1-370 verifies. Xwing signs an authorization under a throwaway in-memory
policy root. Destroyer signs a separate DSSE build statement with another
throwaway in-memory key. No host receives another host's private key.
There is NO operator root, trusted epoch, verified clock, signer attestation
or running container in this extension: any positive result remains HOLD.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shlex
import subprocess
import sys
import time
from typing import Any

try:
    from .verify_signed_builder_policy import (
        POLICY_TYPE, SCHEMA, ProvenanceHold, verify_policy_and_image,
    )
    from .verify_signed_image_provenance import (
        PAYLOAD_TYPE, STATEMENT_TYPE, PREDICATE_TYPE, REPOS,
    )
except ImportError:
    from verify_signed_builder_policy import (
        POLICY_TYPE, SCHEMA, ProvenanceHold, verify_policy_and_image,
    )
    from verify_signed_image_provenance import (
        PAYLOAD_TYPE, STATEMENT_TYPE, PREDICATE_TYPE, REPOS,
    )

COMMIT = "109058ab6c2d8e7b71bfb7146edc5acbe26670db"
MANIFEST = "ed073ed557145da73a3847d8d61ada786d77f23c777aa67e581aaf664a5c60b6"
SERVICE = "assistx-api"
BUILDER = "https://example.test/ephemeral-untrusted-disposable-builder"
BUILD_TYPE = "https://example.test/disposable-build-from-scratch-v1"
RESEARCH_EPOCH = 7
REMOTE_PYTHON = r"""
import base64,hashlib,json,sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
payload=sys.stdin.buffer.read(8193)
kind=sys.argv[1].encode("utf-8")
if not 0<len(payload)<=8192 or kind not in (
    b"application/vnd.in-toto+json",
    b"application/vnd.assistx.builder-authorization+json",
):
    raise ValueError("invalid bounded remote signature request")
k=Ed25519PrivateKey.generate()
pub=k.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
pae=b"DSSEv1 "+str(len(kind)).encode()+b" "+kind+b" "+str(len(payload)).encode()+b" "+payload
obj={
    "ephemeral_public_key":base64.b64encode(pub).decode(),
    "envelope":{
        "payloadType":kind.decode(),
        "payload":base64.b64encode(payload).decode(),
        "signatures":[{
            "keyid":hashlib.sha256(pub).hexdigest(),
            "sig":base64.b64encode(k.sign(pae)).decode(),
        }],
    },
}
sys.stdout.write(json.dumps(obj,sort_keys=True))
"""


class ResearchHold(RuntimeError):
    """Unauthorized or unexpectedly changed disposable signature response."""


def _remote_sign(host: str, kind: str, payload: bytes) -> tuple[dict[str, Any], bytes]:
    if (host not in ("xwing", "destroyer")
            or kind not in (PAYLOAD_TYPE, POLICY_TYPE)
            or not isinstance(payload, bytes)
            or not 0 < len(payload) <= 8192):
        raise ResearchHold("unapproved host or remote signing request")
    cmd = [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=yes", host,
        "python3 -c " + shlex.quote(REMOTE_PYTHON) + " " + shlex.quote(kind),
    ]
    try:
        r = subprocess.run(
            cmd, input=payload, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=19, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResearchHold("remote ephemeral signer unavailable") from exc
    if r.returncode or len(r.stdout) > 16000:
        raise ResearchHold("remote ephemeral signer denied request")
    try:
        doc = json.loads(r.stdout)
        if not isinstance(doc, dict) or set(doc) != {
            "envelope", "ephemeral_public_key",
        }:
            raise ResearchHold("remote signer response shape mismatch")
        key = base64.b64decode(doc["ephemeral_public_key"], validate=True)
        if len(key) != 32:
            raise ResearchHold("remote key length invalid")
        env = doc["envelope"]
        if (not isinstance(env, dict)
                or env.get("payloadType") != kind
                or base64.b64decode(env["payload"], validate=True) != payload):
            raise ResearchHold("remote signer changed signed claim")
        return env, key
    except (ValueError, KeyError, TypeError) as exc:
        raise ResearchHold("remote signer returned invalid envelope") from exc


def _payload(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _statement() -> dict[str, Any]:
    return {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": "service:" + SERVICE,
                     "digest": {"sha256": MANIFEST}}],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": BUILD_TYPE,
                "externalParameters": {
                    "sourceRepository": REPOS[SERVICE],
                    "sourceRevision": COMMIT,
                    "service": SERVICE,
                },
                "resolvedDependencies": [{
                    "uri": "git+" + REPOS[SERVICE],
                    "digest": {"gitCommit": COMMIT},
                }],
            },
            "runDetails": {"builder": {"id": BUILDER}},
        },
    }


def _policy(builder_public: bytes, now: int) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "epoch": RESEARCH_EPOCH,
        "not_before": now - 30, "expires_at": now + 600,
        "service": SERVICE, "source_commit": COMMIT,
        "manifest_sha256": MANIFEST,
        "builder_id": BUILDER, "build_type": BUILD_TYPE,
        "builder_key_sha256": hashlib.sha256(builder_public).hexdigest(),
    }


def _fixture_runtime() -> dict[str, Any]:
    """NOT live image evidence: this object is a synthetic test record."""
    return {
        "name": SERVICE, "state": "running",
        "container_id": "b"*64,
        "image": {
            "local_image_id": "sha256:"+"c"*64,
            "manifest_digest_values": ["sha256:" + MANIFEST],
        },
    }


def run() -> dict[str, Any]:
    now = int(time.time())
    statement, builder_public = _remote_sign(
        "destroyer", PAYLOAD_TYPE, _payload(_statement()),
    )
    authorization, root_public = _remote_sign(
        "xwing", POLICY_TYPE, _payload(_policy(builder_public, now)),
    )
    root_fingerprint = hashlib.sha256(root_public).hexdigest()
    builder_fingerprint = hashlib.sha256(builder_public).hexdigest()
    if root_fingerprint == builder_fingerprint:
        raise ResearchHold("independent ephemeral signing keys collided")
    common = {
        "policy_envelope": authorization,
        "root_public_key": root_public,
        "expected_root_sha256": root_fingerprint,
        "builder_public_key": builder_public,
        "attestation_envelope": statement,
        "runtime_record": _fixture_runtime(),
        "expected_service": SERVICE,
        "expected_commit": COMMIT,
        "expected_manifest_sha256": MANIFEST,
        "expected_builder_id": BUILDER,
        "expected_build_type": BUILD_TYPE,
        "externally_required_epoch": RESEARCH_EPOCH,
        "check_time_epoch": now,
        "revoked_builder_key_sha256": frozenset(),
    }
    result = verify_policy_and_image(**common)
    if result["production_decision"] != "HOLD" or result["promotion_authorized"]:
        raise ResearchHold("signer test improperly authorized release")
    negatives: dict[str, bool] = {}
    for name, altered in (
        ("revoked_builder", {"revoked_builder_key_sha256": frozenset({builder_fingerprint})}),
        ("stale_epoch", {"externally_required_epoch": RESEARCH_EPOCH + 1}),
        ("expired", {"check_time_epoch": now + 601}),
        ("swapped_manifest", {
            "runtime_record": {
                **_fixture_runtime(),
                "image": {
                    "local_image_id": "sha256:"+"c"*64,
                    "manifest_digest_values": ["sha256:" + "f"*64],
                },
            },
        }),
        ("wrong_root", {"expected_root_sha256": "0"*64}),
    ):
        try:
            verify_policy_and_image(**{**common, **altered})
        except ProvenanceHold:
            negatives[name] = True
        else:
            raise ResearchHold("unsafe signature/epoch/manifest accepted")
    return {
        "evidence_tier": "THREE_NODE_PHYSICAL_EPHEMERAL_SIGNATURES_SYNTHETIC_RUNTIME",
        "root_signer_host": "xwing",
        "builder_signer_host": "destroyer",
        "verifier_host": "x1-370",
        "both_ephemeral_signatures_cryptographically_valid": True,
        "negative_tests_denied": negatives,
        "actual_running_container_observed_by_this_trial": False,
        "operator_root_custody_independently_verified": False,
        "external_epoch_witness_proven_nonrestorable": False,
        "production_decision": "HOLD",
        "promotion_authorized": False,
    }


def main() -> int:
    try:
        out = run()
    except (ResearchHold, ProvenanceHold) as exc:
        print(json.dumps({
            "production_decision": "HOLD", "promotion_authorized": False,
            "error": type(exc).__name__,
        }, sort_keys=True))
        return 3
    print(json.dumps(out, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
