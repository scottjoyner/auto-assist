#!/usr/bin/env python3
"""Offline Hermes restricted-image provenance verifier: ALWAYS release HOLD.

No trust root is embedded, fetched or accepted from the signed payload.
A valid signature attests to bytes under an externally pinned key but is not
image availability, independent signer custody, owner grant, rollback fencing,
network-loss acceptance, or deployment approval.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

AUDIENCE = 'assistx-hermes-restricted-image-v1'
REPOSITORY = 'scottjoyner/auto-assist'
MAX_MANIFEST = 4096
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
HEX128 = re.compile(r'[0-9a-f]{128}\Z')
FIELDS = frozenset({
    'schema','audience','repository','issuer_id','generation',
    'source_commit','source_tree','adapter_blob','image_id',
    'restricted_compose_sha256','signature',
})


class Denied(ValueError):
    pass


def need(ok: bool, reason: str) -> None:
    if not ok:
        raise Denied(reason)


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    out = {}
    for name, value in pairs:
        need(name not in out, 'duplicate signed manifest field')
        out[name] = value
    return out


def verify_signed_manifest(raw: bytes, *,
        externally_trusted_public_key: bytes,
        pinned_public_key_sha256: str, expected_issuer_id: str,
        expected_source_commit: str, expected_source_tree: str,
        expected_adapter_blob: str, expected_image_id: str,
        expected_compose_sha256: str,
        minimum_independently_recorded_generation: int) -> dict:
    """Verify signature, pin claims, reject stale revisions; never say GO.

    The 'expected' values and high water are asserted by the caller and MUST
    originate independently of the manifest. This function cannot attest
    their provenance or authenticate an image stored by Docker.
    """
    need(type(raw) is bytes and 0 < len(raw) <= MAX_MANIFEST,
         'missing or overlarge manifest')
    need(type(externally_trusted_public_key) is bytes and
         len(externally_trusted_public_key) == 32,
         'externally pinned issuer public key is required')
    need(type(pinned_public_key_sha256) is str and
         HEX64.fullmatch(pinned_public_key_sha256) is not None and
         hmac.compare_digest(hashlib.sha256(externally_trusted_public_key).hexdigest(),
                             pinned_public_key_sha256),
         'trusted issuer key fingerprint does not match')
    need(type(expected_issuer_id) is str and
         re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{2,79}', expected_issuer_id)
         is not None, 'untrusted issuer selector')
    for val in (expected_source_commit, expected_source_tree,
                expected_adapter_blob):
        need(type(val) is str and HEX40.fullmatch(val) is not None,
             'missing independently expected source identity')
    need(type(expected_image_id) is str and
         expected_image_id.startswith('sha256:') and
         HEX64.fullmatch(expected_image_id[7:]) is not None and
         type(expected_compose_sha256) is str and
         HEX64.fullmatch(expected_compose_sha256) is not None,
         'missing independently expected image/Compose identity')
    need(type(minimum_independently_recorded_generation) is int and
         minimum_independently_recorded_generation >= 1,
         'independent monotonic generation witness missing')
    try:
        manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(Denied('invalid JSON constant')))
    except (ValueError, UnicodeError) as exc:
        raise Denied('invalid manifest encoding') from exc
    need(type(manifest) is dict and set(manifest) == FIELDS,
         'manifest shape/fields differ from signed schema')
    need(type(manifest['schema']) is int and manifest['schema'] == 1 and
         manifest['audience'] == AUDIENCE and manifest['repository'] == REPOSITORY and
         manifest['issuer_id'] == expected_issuer_id,
         'wrong schema, audience, repository or independently trusted issuer')
    need(type(manifest['generation']) is int and
         manifest['generation'] >= minimum_independently_recorded_generation,
         'signed release generation is stale or invalid')
    for field, expected in (
        ('source_commit', expected_source_commit),
        ('source_tree', expected_source_tree),
        ('adapter_blob', expected_adapter_blob),
        ('image_id', expected_image_id),
        ('restricted_compose_sha256', expected_compose_sha256),
    ):
        need(type(manifest[field]) is str and manifest[field] == expected,
             f'signed {field} differs from the independent expected identity')
    signature = manifest['signature']
    need(type(signature) is str and HEX128.fullmatch(signature) is not None,
         'missing valid detached Ed25519 signature')
    signed_fields = {key: value for key, value in manifest.items()
                     if key != 'signature'}
    try:
        Ed25519PublicKey.from_public_bytes(externally_trusted_public_key).verify(
            bytes.fromhex(signature), canonical(signed_fields))
    except (InvalidSignature, ValueError) as exc:
        raise Denied('Ed25519 signature invalid under independently pinned key') from exc
    return {
        'decision': 'HOLD',
        'schema': 1,
        'ed25519_signature_under_pinned_key_verified': True,
        'expected_image_identity_matches_manifest': True,
        'expected_source_and_compose_match_manifest': True,
        'release_generation_not_below_supplied_high_water': True,
        'signed_image_id': manifest['image_id'],
        'signed_source_commit': manifest['source_commit'],
        'signed_generation': manifest['generation'],
        'independent_issuer_custody_and_revocation_verified': False,
        'image_digest_physically_loaded_and_checked': False,
        'image_binary_reproducibly_built_or_transparently_logged': False,
        'owner_custody_approved_for_ro_and_rw_paths': False,
        'real_container_mount_and_restart_tested': False,
        'deployment_authorized': False,
    }


def _read_bounded_regular_file(path: Path, *, max_bytes: int) -> bytes:
    need(path.is_file() and not path.is_symlink() and
         0 < path.stat().st_size <= max_bytes,
         'manifest or independently supplied public key is missing/unsafe')
    return path.read_bytes()


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--manifest', type=Path)
    cli.add_argument('--externally-trusted-public-key', type=Path)
    cli.add_argument('--pinned-public-key-sha256')
    cli.add_argument('--expected-issuer-id')
    cli.add_argument('--expected-source-commit')
    cli.add_argument('--expected-source-tree')
    cli.add_argument('--expected-adapter-blob')
    cli.add_argument('--expected-image-id')
    cli.add_argument('--expected-compose-sha256')
    cli.add_argument('--minimum-independently-recorded-generation', type=int)
    args = cli.parse_args()
    if args.manifest is None:
        print(json.dumps({'decision':'HOLD', 'verification_executed':False,
                          'deployment_authorized':False}, sort_keys=True))
        return
    required = (
        args.externally_trusted_public_key,
        args.pinned_public_key_sha256,
        args.expected_issuer_id,
        args.expected_source_commit,
        args.expected_source_tree,
        args.expected_adapter_blob,
        args.expected_image_id,
        args.expected_compose_sha256,
        args.minimum_independently_recorded_generation,
    )
    need(all(v is not None for v in required),
         'all externally pinned release and monotonic witness inputs are required')
    manifest_bytes = _read_bounded_regular_file(args.manifest,
                                                max_bytes=MAX_MANIFEST)
    public_key = _read_bounded_regular_file(args.externally_trusted_public_key,
                                            max_bytes=32)
    report = verify_signed_manifest(
        manifest_bytes, externally_trusted_public_key=public_key,
        pinned_public_key_sha256=args.pinned_public_key_sha256,
        expected_issuer_id=args.expected_issuer_id,
        expected_source_commit=args.expected_source_commit,
        expected_source_tree=args.expected_source_tree,
        expected_adapter_blob=args.expected_adapter_blob,
        expected_image_id=args.expected_image_id,
        expected_compose_sha256=args.expected_compose_sha256,
        minimum_independently_recorded_generation=(
            args.minimum_independently_recorded_generation),
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (Denied, OSError, ValueError) as exc:
        raise SystemExit(f'HOLD: {exc}') from None
