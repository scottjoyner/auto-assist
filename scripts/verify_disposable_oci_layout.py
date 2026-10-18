"""Offline OCI-layout and disposable Docker-runtime binding, fail-closed.

Pure OCI inspection is available without Docker or a signer. The ONLY
runtime binding accepted by the lab requires the loaded ephemeral image
ID to equal the SHA256 of the one-manifest OCI artifact. This is the
actual behavior tested on the pinned Docker Buildx/Engine version here,
not an assumed cross-runtime OCI/registry equivalence.

No archive member is extracted to disk. Does NOT grant production trust.
"""
from __future__ import annotations

import zlib
import hashlib
import io
import json
import os
import re
import stat
import tarfile
from typing import Any

MAX_OCI = 32 * 1024 * 1024
MAX_BLOB = 16 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")
GIT_BLOB = re.compile(r"[0-9a-f]{40}\Z")
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"


class ArtifactHold(ValueError):
    """Malformed, modified, missing or untrusted synthetic OCI evidence."""


def _object(raw: bytes) -> dict[str, Any]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ArtifactHold("duplicate OCI JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise ArtifactHold("unparseable OCI JSON") from exc
    if not isinstance(value, dict):
        raise ArtifactHold("OCI JSON root is not an object")
    return value


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def _tar_entries(data: bytes) -> dict[str, bytes]:
    try:
        t = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
    except (tarfile.TarError, OSError) as exc:
        raise ArtifactHold("unreadable OCI archive") from exc
    found: dict[str, bytes] = {}
    with t:
        if len(t.getmembers()) > 24:
            raise ArtifactHold("unbounded OCI archive member count")
        for member in t:
            if member.isdir():
                if member.name not in ("blobs", "blobs/", "blobs/sha256",
                                       "blobs/sha256/"):
                    raise ArtifactHold("unexpected OCI archive directory")
                continue
            if not member.isfile() or member.size > MAX_BLOB:
                raise ArtifactHold("nonregular or oversized OCI member")
            name = member.name
            if name not in ("oci-layout", "index.json"):
                if not name.startswith("blobs/sha256/"):
                    raise ArtifactHold("unexpected OCI archive member")
                digest = name[len("blobs/sha256/"):]
                if not HEX.fullmatch(digest):
                    raise ArtifactHold("bad OCI blob path")
            if name in found:
                raise ArtifactHold("duplicate OCI member")
            stream = t.extractfile(member)
            if stream is None:
                raise ArtifactHold("unreadable OCI member")
            value = stream.read(MAX_BLOB + 1)
            if len(value) != member.size:
                raise ArtifactHold("OCI member size mismatch")
            found[name] = value
    return found


def _digest_blob(entries: dict[str, bytes], descriptor: Any,
                 media_type: str) -> bytes:
    if not isinstance(descriptor, dict):
        raise ArtifactHold("OCI descriptor is not an object")
    if set(descriptor) != {"mediaType", "digest", "size"}:
        raise ArtifactHold("OCI descriptor includes unknown or missing fields")
    digest = descriptor["digest"]
    if (descriptor["mediaType"] != media_type
            or not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or not HEX.fullmatch(digest[7:])
            or type(descriptor["size"]) is not int
            or not (0 < descriptor["size"] <= MAX_BLOB)):
        raise ArtifactHold("invalid OCI blob descriptor")
    data = entries.get("blobs/sha256/" + digest[7:])
    if data is None or len(data) != descriptor["size"]:
        raise ArtifactHold("missing or wrong-size OCI blob")
    if hashlib.sha256(data).hexdigest() != digest[7:]:
        raise ArtifactHold("OCI blob digest mismatch")
    return data


def _find_source_in_layers(layers: list[bytes], expected_blob: str) -> list[str]:
    if not GIT_BLOB.fullmatch(expected_blob):
        raise ArtifactHold("expected original Git blob hash invalid")
    matches = 0
    decompressed_digests = []
    for compressed in layers:
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            plain = decoder.decompress(compressed, MAX_BLOB + 1)
            if len(plain) > MAX_BLOB or decoder.unconsumed_tail:
                raise ArtifactHold("decompressed OCI layer too large")
            plain += decoder.flush(MAX_BLOB + 1 - len(plain))
            if not decoder.eof or decoder.unused_data:
                raise ArtifactHold("incomplete or trailing OCI layer compression")
        except zlib.error as exc:
            raise ArtifactHold("unreadable compressed OCI layer") from exc
        decompressed_digests.append("sha256:" + hashlib.sha256(plain).hexdigest())
        try:
            archive = tarfile.open(fileobj=io.BytesIO(plain), mode="r:")
        except tarfile.TarError as exc:
            raise ArtifactHold("malformed OCI layer TAR") from exc
        with archive:
            for member in archive:
                if member.name not in ("source/verify.py", "source/verify.py/"):
                    continue
                if not member.isfile() or member.size > MAX_BLOB:
                    raise ArtifactHold("source layer is not regular file")
                member_stream = archive.extractfile(member)
                if member_stream is None:
                    raise ArtifactHold("source layer cannot be read")
                file_bytes = member_stream.read(MAX_BLOB + 1)
                if git_blob_sha(file_bytes) != expected_blob:
                    raise ArtifactHold("built-in source file differs from Git blob")
                matches += 1
    if matches != 1:
        raise ArtifactHold("expected exactly one source file in image layers")
    return decompressed_digests


def inspect_oci(data: bytes, *, expected_source_blob: str) -> dict[str, Any]:
    if not isinstance(data, bytes) or not (0 < len(data) <= MAX_OCI):
        raise ArtifactHold("OCI image artifact exceeds bounded input")
    entries = _tar_entries(data)
    if _object(entries.get("oci-layout", b"")) != {"imageLayoutVersion": "1.0.0"}:
        raise ArtifactHold("unknown OCI image-layout version")
    index = _object(entries.get("index.json", b""))
    if (index.get("schemaVersion") != 2
            or index.get("mediaType") != "application/vnd.oci.image.index.v1+json"
            or not isinstance(index.get("manifests"), list)
            or len(index["manifests"]) != 1):
        raise ArtifactHold("OCI index is not a single-manifest image")
    ref = index["manifests"][0]
    if not isinstance(ref, dict) or set(ref) - {
        "mediaType", "digest", "size", "annotations", "platform",
    } or "digest" not in ref:
        raise ArtifactHold("unexpected OCI index descriptor")
    desc = {k: ref[k] for k in ("mediaType", "digest", "size")}
    manifest_data = _digest_blob(entries, desc, OCI_MANIFEST)
    manifest = _object(manifest_data)
    if set(manifest) != {"schemaVersion", "mediaType", "config", "layers"}:
        raise ArtifactHold("unexpected OCI image manifest fields")
    if manifest["schemaVersion"] != 2 or manifest["mediaType"] != OCI_MANIFEST:
        raise ArtifactHold("wrong image manifest version")
    config = _digest_blob(entries, manifest["config"], OCI_CONFIG)
    config_doc = _object(config)
    if not isinstance(manifest["layers"], list) or len(manifest["layers"]) not in (1, 2, 3):
        raise ArtifactHold("unbounded or missing OCI image layers")
    layers = [_digest_blob(entries, layer, OCI_LAYER) for layer in manifest["layers"]]
    uncompressed_digests = _find_source_in_layers(layers, expected_source_blob)
    expected_blobs = {
        "blobs/sha256/" + d[7:]
        for d in (
            ref["digest"], manifest["config"]["digest"],
            *(layer["digest"] for layer in manifest["layers"]),
        )
    }
    if set(entries) != {"oci-layout", "index.json"} | expected_blobs:
        raise ArtifactHold("orphan OCI blob or missing referenced member")
    config_digest = manifest["config"]["digest"]
    rootfs = config_doc.get("rootfs") or {}
    if (rootfs.get("type") != "layers"
            or not isinstance(rootfs.get("diff_ids"), list)
            or rootfs["diff_ids"] != uncompressed_digests):
        raise ArtifactHold("OCI config uncompressed layer digest mismatch")
    runtime_conf = config_doc.get("config") or {}
    if (runtime_conf.get("User") != "65534:65534"
            or runtime_conf.get("Entrypoint") != ["/probe"]):
        raise ArtifactHold("OCI disposable runtime user/entrypoint policy mismatch")
    result = {
        "manifest_sha256": ref["digest"][7:],
        "config_sha256": config_digest[7:],
        "source_git_blob_sha": expected_source_blob,
        "layer_count": len(layers),
        "source_file_verified_inside_oci_layer": True,
        "production_decision": "HOLD",
        "promotion_authorized": False,
    }
    return result


def inspect_oci_file(path: str, *, expected_source_blob: str) -> dict[str, Any]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ArtifactHold("absolute local OCI path required")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or not (0 < st.st_size <= MAX_OCI):
                raise ArtifactHold("OCI input is oversized or nonregular")
            data = f.read(MAX_OCI + 1)
    except OSError as exc:
        raise ArtifactHold("OCI input file unavailable") from exc
    return inspect_oci(data, expected_source_blob=expected_source_blob)
