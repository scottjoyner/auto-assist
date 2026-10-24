#!/usr/bin/env python3
"""Pin a synthetic Hermes image-overlay build context without Docker or secrets.

The original running image is only an unapproved base. This script verifies
allowlisted source bytes, shows what *could* be copied into a candidate image,
and denies production promotion. No real keys or corpus are staged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_IMAGE_ID = 'sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307'
ENTRYPOINT = 'tests/hermes_split_root_candidate_entrypoint.sh'
ENTRYPOINT_SHA256 = '81add7ecd43ef1b3fccc5d8097b4f51ed196cfb61d5ccd3cd243e51a552ab6f5'
# Git blob IDs already reviewed in the strict normal-entrypoint synthetic canary.
BLOBS = {
    'src/assistx/knowledge_roots.py':'a7827f936f23a3e85103e26efa73e6c02d3a4124',
    'src/assistx/swarm_memory.py':'1ff75acd591c5ddc18844a0378a0a1ca4a427ee9',
    'src/assistx/hermes_corpus_custody.py':'e504334468ae0962668f2abd37978532483ec29b',
    'src/assistx/hermes_corpus_checkpoint.py':'ff7091618919b2261b1cbf4df2602627afae8c1f',
    'src/assistx/hermes_high_water.py':'ed8992eef765006cb6a12def4553b04d0129ecbe',
    'src/assistx/strict_executor_adapter.py':'9c554adee1514085b7948a30d4bf08bca2de2711',
    'src/assistx/hermes_launch_gate.py':'aef4baf09ffca725a2eea0033904672406ecf1bd',
    'src/assistx/owner_identity.py':'392fa5780d5ddd450f798281b711b99512b2e692',
    'src/assistx/agents/hermes_agent_adapter.py':'8cf829275b188c3b340f2cde17560665acdcfa85',
}


class Denied(ValueError):
    pass


def need(predicate: bool, reason: str) -> None:
    if not predicate:
        raise Denied(reason)


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\x00'+data).hexdigest()


def source_inventory(source_root: Path = ROOT) -> dict:
    """Metadata/content of *only* expected source paths; no secret traversal."""
    need(isinstance(source_root, Path) and source_root.is_dir() and
         not source_root.is_symlink(), 'repository root inaccessible')
    observed = {}
    for relative, exact_blob in BLOBS.items():
        path = source_root / relative
        need(path.is_file() and not path.is_symlink() and
             path.stat().st_size <= 200000,
             'reviewed source missing, redirected or overlarge: '+relative)
        raw = path.read_bytes()
        need(git_blob(raw) == exact_blob,
             'reviewed source mutated: '+relative)
        observed[relative] = {
            'git_blob':exact_blob,'sha256':hashlib.sha256(raw).hexdigest(),
            'size':len(raw),
        }
    path = source_root / ENTRYPOINT
    need(path.is_file() and not path.is_symlink() and
         path.stat().st_size < 10000,
         'restricted rootless entrypoint absent/redirected')
    data = path.read_bytes()
    need(hashlib.sha256(data).hexdigest()==ENTRYPOINT_SHA256,
         'restricted rootless entrypoint differs from reviewed bytes')
    need(b'root execution denied in hardened Hermes candidate' in data and
         b'non-root startup refuses credential environment seeding' in data and
         b'exec "$@"' in data,
         'rootless startup and no-credential-seeding contract drifted')
    legacy = source_root / 'entrypoint.sh'
    need(legacy.is_file() and not legacy.is_symlink() and
         b'/root/.hermes' in legacy.read_bytes(),
         'legacy startup identity unreviewed')
    return {
        'reviewed_hardened_source_count':len(observed),
        'reviewed_sources':observed,
        'candidate_entrypoint_sha256':ENTRYPOINT_SHA256,
        'current_legacy_root_entrypoint_is_not_candidate':True,
        'unreviewed_image_base_digest':BASE_IMAGE_ID,
        'reproducible_image_binary_proven':False,
        'real_signing_key_or_production_attestations_included':False,
        'image_overlay_built_or_run':False,
        'production_release_authorized':False,
        'decision':'HOLD',
    }


def dockerfile_recipe(*, source_root: Path = ROOT) -> str:
    """String recipe only. Caller MUST NOT treat it as approved image build."""
    inventory = source_inventory(source_root)
    need(inventory['reviewed_hardened_source_count']==9,
         'incomplete signed-source candidate context')
    lines = ['# STATIC RESEARCH RECIPE: no build/pull/run/production release',
             'FROM '+BASE_IMAGE_ID]
    for relative in BLOBS:
        dst = '/app/'+relative.replace('src/','src/')
        lines.append('COPY '+relative+' '+dst)
    lines += [
        'COPY '+ENTRYPOINT+' /entrypoint.sh',
        'RUN chmod 0755 /entrypoint.sh',
        'ENTRYPOINT ["/entrypoint.sh"]',
        'USER 1000:1000',
    ]
    return '\n'.join(lines)+'\n'


def validate_recipe(recipe: str) -> None:
    """Reject broad wildcard COPY, source drift, root fallback and network RUN."""
    need(type(recipe) is str and len(recipe) < 8000,
         'unbounded candidate build recipe')
    need(recipe == dockerfile_recipe(), 'candidate Dockerfile does not match exact allowlist')
    need(len(re.findall(r'^COPY ',recipe,re.M)) == len(BLOBS)+1,
         'unexpected image overlay source input')
    need('COPY . ' not in recipe and '/root/.ssh' not in recipe and
         'entrypoint.sh /entrypoint.sh' in recipe and
         'USER 1000:1000' in recipe,
         'unsafe root/SSH/entire-repository overlay')


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--read-only',action='store_true')
    args=p.parse_args()
    if args.read_only:
        manifest=source_inventory()
        recipe=dockerfile_recipe()
        validate_recipe(recipe)
        manifest['allowlisted_recipe_sha256']=hashlib.sha256(recipe.encode()).hexdigest()
        print(json.dumps(manifest,sort_keys=True))
    else:
        print(json.dumps({'decision':'HOLD','source_read':False,
                          'image_overlay_built_or_run':False,
                          'production_release_authorized':False}))


if __name__=='__main__':
    try:
        main()
    except (Denied, OSError) as exc:
        raise SystemExit(f'HOLD: {exc}') from None
