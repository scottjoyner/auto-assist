"""Untrusted disposable cross-node OCI verifier and ephemeral DSSE signer.

Intentionally standalone: only Python stdlib + cryptography on Destroyer.
Reads a *bounded* OCI archive from stdin, verifies contents independently
of the x1-370 builder, reconstructs the statement itself, then signs it
using an in-memory, never-persisted Ed25519 key.

Not a trusted build authority: the invoking node ships this code and the
source expectations, the signer identity is ephemeral, the fixture is
not a complete/authorized production build, and nothing is logged/registered.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
import tarfile
import zlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MAX_ARCHIVE = 8 * 1024 * 1024
MAX_MEMBER = 4 * 1024 * 1024
MAX_LAYER = 8 * 1024 * 1024
EXPECTED_SOURCE_BLOB = "dad70bbde2f51f595545e6ccb0ff41892d8681be"
EXPECTED_GIT_SHA = "edcc142af74695a71ffa3316fb7711ed1bcc9fbb"
REPO = "https://github.com/scottjoyner/auto-assist"
SERVICE = "assistx-api"
BUILDER = "https://example.test/ephemeral-untrusted-destroyer-signer"
BUILD_TYPE = "https://example.test/disposable-build-from-scratch-v1"
PAYLOAD_TYPE = b"application/vnd.in-toto+json"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class Denied(ValueError):
    """Any mismatch, ambiguity or malformed OCI data denies signing."""


def _strict_json(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Denied("duplicate JSON key")
            result[key] = value
        return result

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise Denied("invalid OCI JSON") from exc
    if not isinstance(result, dict):
        raise Denied("OCI JSON root must be an object")
    return result


def _members(tar_bytes: bytes, *, layer: bool) -> dict[str, bytes]:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
    except (tarfile.TarError, OSError) as exc:
        raise Denied("invalid TAR archive") from exc
    members = {}
    with archive:
        if len(archive.getmembers()) > 24:
            raise Denied("too many archive members")
        for member in archive:
            if member.isdir():
                continue
            if not member.isfile() or member.size > MAX_MEMBER:
                raise Denied("nonregular or excessive archive member")
            name = member.name
            if name in members or name.startswith("/") or ".." in name.split("/"):
                raise Denied("ambiguous or unsafe archive member")
            if not layer and name not in ("oci-layout", "index.json"):
                if not name.startswith("blobs/sha256/") or not HEX64.fullmatch(name[13:]):
                    raise Denied("not an OCI content-addressed blob")
            content = archive.extractfile(member)
            if content is None:
                raise Denied("missing archive member contents")
            data = content.read(MAX_MEMBER + 1)
            if len(data) != member.size:
                raise Denied("archive member length mismatch")
            members[name] = data
    return members


def _blob(members: dict[str, bytes], descriptor: dict, media: str) -> bytes:
    if not isinstance(descriptor, dict):
        raise Denied("malformed descriptor")
    if set(descriptor) != {"mediaType", "digest", "size"}:
        raise Denied("unexpected descriptor fields")
    sha = descriptor["digest"]
    size = descriptor["size"]
    if (descriptor["mediaType"] != media or not isinstance(sha, str)
            or not sha.startswith("sha256:") or not HEX64.fullmatch(sha[7:])
            or type(size) is not int or not (0 < size <= MAX_MEMBER)):
        raise Denied("unrecognized OCI descriptor")
    body = members.get("blobs/sha256/" + sha[7:])
    if body is None or len(body) != size:
        raise Denied("missing referenced OCI blob")
    if hashlib.sha256(body).hexdigest() != sha[7:]:
        raise Denied("OCI SHA256 mismatch")
    return body


def inspect(source_oci_bytes: bytes) -> dict:
    if not isinstance(source_oci_bytes, bytes) or not (
        0 < len(source_oci_bytes) <= MAX_ARCHIVE
    ):
        raise Denied("OCI archive over bounded input")
    items = _members(source_oci_bytes, layer=False)
    if _strict_json(items.get("oci-layout", b"")) != {"imageLayoutVersion": "1.0.0"}:
        raise Denied("unsupported OCI layout version")
    index = _strict_json(items.get("index.json", b""))
    if (index.get("schemaVersion") != 2
            or index.get("mediaType") != "application/vnd.oci.image.index.v1+json"
            or not isinstance(index.get("manifests"), list)
            or len(index["manifests"]) != 1):
        raise Denied("OCI index must reference exactly one image")
    ref = index["manifests"][0]
    if not isinstance(ref, dict) or set(ref) - {
        "mediaType", "digest", "size", "annotations", "platform",
    }:
        raise Denied("unexpected OCI index fields")
    manifest_ref = {k: ref[k] for k in ("mediaType", "digest", "size")}
    manifest = _strict_json(_blob(
        items, manifest_ref, "application/vnd.oci.image.manifest.v1+json",
    ))
    if set(manifest) != {"schemaVersion", "mediaType", "config", "layers"}:
        raise Denied("unexpected manifest content")
    if (manifest["schemaVersion"] != 2
            or manifest["mediaType"] != "application/vnd.oci.image.manifest.v1+json"
            or not isinstance(manifest["layers"], list)
            or len(manifest["layers"]) != 2):
        raise Denied("wrong OCI test manifest shape")
    config = _strict_json(_blob(
        items, manifest["config"], "application/vnd.oci.image.config.v1+json",
    ))
    details = config.get("config") or {}
    if details.get("User") != "65534:65534" or details.get("Entrypoint") != ["/probe"]:
        raise Denied("wrong OCI sandbox user/entrypoint")

    raw_diffs = []
    source_matches = 0
    for desc in manifest["layers"]:
        compressed = _blob(
            items, desc, "application/vnd.oci.image.layer.v1.tar+gzip",
        )
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decoded = d.decompress(compressed, MAX_LAYER + 1)
            if len(decoded) > MAX_LAYER or d.unconsumed_tail:
                raise Denied("OCI gzip expands beyond budget")
            decoded += d.flush(MAX_LAYER + 1 - len(decoded))
            if not d.eof or d.unused_data:
                raise Denied("incomplete or trailing OCI gzip")
        except zlib.error as exc:
            raise Denied("invalid OCI layer gzip") from exc
        raw_diffs.append("sha256:" + hashlib.sha256(decoded).hexdigest())
        for name, data in _members(decoded, layer=True).items():
            if name != "source/verify.py":
                continue
            actual = hashlib.sha1(
                b"blob " + str(len(data)).encode("ascii") + b"\x00" + data,
            ).hexdigest()
            if actual != EXPECTED_SOURCE_BLOB:
                raise Denied("embedded source is not pinned Git blob")
            source_matches += 1
    if source_matches != 1:
        raise Denied("signed image did not contain exactly one pinned source file")
    if (not isinstance(config.get("rootfs"), dict)
            or config["rootfs"].get("type") != "layers"
            or config["rootfs"].get("diff_ids") != raw_diffs):
        raise Denied("image config diff ID does not match unpacked bytes")
    references = {
        "blobs/sha256/" + d["digest"][7:]
        for d in [manifest_ref, manifest["config"], *manifest["layers"]]
    }
    if set(items) != references | {"oci-layout", "index.json"}:
        raise Denied("unreferenced or missing image blobs")
    return {
        "manifest_sha256": manifest_ref["digest"][7:],
        "embedded_source_git_blob": EXPECTED_SOURCE_BLOB,
        "layer_count": 2,
    }


def statement_for_artifact(inspected: dict) -> bytes:
    manifest_sha = inspected["manifest_sha256"]
    body = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{
            "name": "service:" + SERVICE,
            "digest": {"sha256": manifest_sha},
        }],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": BUILD_TYPE,
                "externalParameters": {
                    "sourceRepository": REPO,
                    "sourceRevision": EXPECTED_GIT_SHA,
                    "service": SERVICE,
                },
                "resolvedDependencies": [{
                    "uri": "git+" + REPO,
                    "digest": {"gitCommit": EXPECTED_GIT_SHA},
                }],
            },
            "runDetails": {"builder": {"id": BUILDER}},
        },
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def attest(source_oci_bytes: bytes) -> dict:
    inspected = inspect(source_oci_bytes)
    statement = statement_for_artifact(inspected)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pae = (b"DSSEv1 " + str(len(PAYLOAD_TYPE)).encode() + b" "
           + PAYLOAD_TYPE + b" " + str(len(statement)).encode() + b" "
           + statement)
    signature = key.sign(pae)
    return {
        "envelope": {
            "payloadType": PAYLOAD_TYPE.decode(),
            "payload": base64.b64encode(statement).decode(),
            "signatures": [{
                "keyid": hashlib.sha256(public).hexdigest(),
                "sig": base64.b64encode(signature).decode(),
            }],
        },
        "ephemeral_public_key": base64.b64encode(public).decode(),
        "remote_observed_manifest_sha256": inspected["manifest_sha256"],
        "remote_verified_source_git_blob": inspected["embedded_source_git_blob"],
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_ARCHIVE + 1)
        result = attest(raw)
    except (Denied, OSError, KeyError, TypeError, ValueError):
        # Never emit source bytes, private keys, arbitrary archive data or
        # untrusted exception strings. Nonzero return + category only.
        sys.stderr.write("DISPOSABLE_ARTIFACT_DENIED\n")
        return 3
    sys.stdout.write(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
