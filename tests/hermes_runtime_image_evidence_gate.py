#!/usr/bin/env python3
"""Classify a GitHub runtime-image evidence ZIP without granting Hermes release.

CI built/smoke-tested an AssistX image, NOT an independently signed and
owner-authorized restricted Hermes replacement. No container, secrets, output
storage, provider access or GitHub calls are involved in this classifier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

FILES = frozenset({
    'source-sha.txt', 'image-id.txt',
    'assistx-freeze.txt', 'hermes-freeze.txt',
})
MAX_ZIP = 65536
MAX_FILE = 16384
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')


class UnsafeEvidence(ValueError):
    pass


def need(flag: bool, message: str) -> None:
    if not flag:
        raise UnsafeEvidence(message)


def classify_artifact(raw_zip: bytes, *, pinned_zip_sha256: str,
                      pr_head_sha: str, workflow_success: bool) -> dict:
    """Recognize limited CI *build* evidence; never grant a release permit.

    The named SHA and workflow result must come from separately authenticated
    GitHub metadata. This function cannot authenticate that metadata itself.
    """
    need(type(raw_zip) is bytes and 0 < len(raw_zip) <= MAX_ZIP,
         'artifact ZIP is absent or overlarge')
    need(type(pinned_zip_sha256) is str and
         HEX64.fullmatch(pinned_zip_sha256) is not None,
         'untrusted artifact digest declaration')
    need(hashlib.sha256(raw_zip).hexdigest() == pinned_zip_sha256,
         'artifact ZIP digest differs from GitHub evidence')
    need(type(pr_head_sha) is str and HEX40.fullmatch(pr_head_sha) is not None,
         'invalid PR head')
    need(type(workflow_success) is bool and workflow_success,
         'workflow was not independently reported successful')
    try:
        with ZipFile(BytesIO(raw_zip)) as archive:
            entries = archive.infolist()
            need(len(entries) == 4 and {i.filename for i in entries} == FILES,
                 'unexpected or missing runtime evidence entry')
            out = {}
            for entry in entries:
                # Deny symlinks, ZIP paths, unknown entries, ZIP bombs and links.
                need(entry.filename == Path(entry.filename).name and
                     entry.file_size <= MAX_FILE and entry.compress_size > 0 and
                     entry.file_size <= 80 * entry.compress_size and
                     ((entry.external_attr >> 16) & 0o170000) != 0o120000,
                     'unsafe ZIP entry attributes')
                out[entry.filename] = archive.read(entry)
            need(archive.testzip() is None, 'corrupted evidence ZIP')
    except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, UnsafeEvidence):
            raise
        raise UnsafeEvidence('invalid ZIP container') from exc
    for name in FILES:
        need(len(out[name]) <= MAX_FILE and b'\x00' not in out[name],
             'malformed evidence contents')
    source = out['source-sha.txt'].decode('ascii').strip()
    image = out['image-id.txt'].decode('ascii').strip()
    need(HEX40.fullmatch(source) is not None and
         image.startswith('sha256:') and HEX64.fullmatch(image[7:]) is not None,
         'missing exact image or source identifiers')
    need(out['assistx-freeze.txt'].strip() and out['hermes-freeze.txt'].strip(),
         'missing environment inventories')
    matches = source == pr_head_sha
    return {
        'decision': 'HOLD',
        'ci_build_artifact_sha256_verified': True,
        'ci_workflow_success_asserted_by_caller': True,
        'ci_image_id': image,
        'ci_recorded_source_sha': source,
        'branch_head_sha': pr_head_sha,
        'artifact_source_equals_branch_head': matches,
        'source_identity_requires_merge_checkout_reconciliation': not matches,
        'dependency_inventory_present': True,
        'ci_image_proven_to_be_hermes_replacement': False,
        'independent_image_signature_verified': False,
        'owner_approved_input_and_output_custody': False,
        'host_and_container_mount_admission_verified': False,
        'network_loss_and_restart_persistence_verified': False,
        'release_authorized': False,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--read-artifact', type=Path)
    p.add_argument('--artifact-sha256')
    p.add_argument('--pr-head')
    p.add_argument('--workflow-success', action='store_true')
    args = p.parse_args()
    if args.read_artifact is None:
        print(json.dumps({'decision': 'HOLD', 'artifact_read': False,
                          'release_authorized': False}))
        return
    need(args.read_artifact.is_file() and not args.read_artifact.is_symlink()
         and args.read_artifact.stat().st_size <= MAX_ZIP,
         'artifact is not a regular bounded file')
    result = classify_artifact(args.read_artifact.read_bytes(),
        pinned_zip_sha256=args.artifact_sha256,
        pr_head_sha=args.pr_head, workflow_success=args.workflow_success)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
