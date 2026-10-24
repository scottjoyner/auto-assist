#!/usr/bin/env python3
"""Pin a synthetic Hermes image-overlay build context without Docker or secrets.

The production image is specifically FORBIDDEN as a candidate parent.
The research recipe uses a deliberately nonexistent digest sentinel, to
prevent the historical base image's inherited code, tools and layers from
silently becoming the replacement. This script verifies source bytes but
never builds an image or stages real keys, artifacts or protected corpus.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Observed running legacy image: custody/unreviewed inherited layers, NOT a base.
BASE_IMAGE_ID = 'sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307'
# Deliberately nonexistent image reference; NEVER substitute for a real signed,
# separately approved runtime base via a CI fallback or unreviewed tag.
RESEARCH_BASE_IMAGE_ID = 'sha256:' + '0' * 64
ENTRYPOINT = 'tests/hermes_split_root_candidate_entrypoint.sh'
ENTRYPOINT_SHA256 = '81add7ecd43ef1b3fccc5d8097b4f51ed196cfb61d5ccd3cd243e51a552ab6f5'
# Exact static first-party import closure rooted at strict_executor_main,
# including package __init__ side-effects; no unpinned modules from the old
# image may satisfy these static imports. This is a researched allowlist,
# NOT evidence for dynamic imports, third-party dependencies or image signing.
BLOBS = {
    'src/assistx/__init__.py': '552b736e604f4236e9d98d3e32deef6b0560727d',
    'src/assistx/agents/__init__.py': 'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391',
    'src/assistx/agents/hermes_agent_adapter.py': '8cf829275b188c3b340f2cde17560665acdcfa85',
    'src/assistx/auto_assign_client.py': 'a5e0ede6275973fd9d943fe69f56bdb96ebd895c',
    'src/assistx/config.py': 'fba2e56bce1770d34ca9e238c9b81a51b6e05b62',
    'src/assistx/contracts/__init__.py': '06474c50aa3f0e9580d28c30e56e40035d063b87',
    'src/assistx/contracts/event_envelope.py': 'b57d714f36ce018f13ef97312a7aa332308b536c',
    'src/assistx/contracts/repository_source_verifier.py': 'a245050070e8f2b8e34c94570ca0cc3325ffc615',
    'src/assistx/contracts/schemas/__init__.py': '942d6cf9081bb8187283f08ec0fc19cd79f5390b',
    'src/assistx/contracts/schemas/artifact_paths.py': 'd583ca730d8bb58245881e76a76efbf88e0ca99e',
    'src/assistx/contracts/schemas/auto_ingest_memory_enrichment.py': '8d1de5b4f52ad6228e50f404d55765d0564d430a',
    'src/assistx/contracts/schemas/model_endpoint_registry.py': '63420a9b1d654292805c3ebbb06c5b9cea4639b3',
    'src/assistx/contracts/schemas/node_registry.py': 'aad3e6f66ee05ae627bbf2e3f99246f008f02b95',
    'src/assistx/contracts/schemas/registered_speaker.py': '9bd029269128453b3d86f333601fb3e7cd7bbfd4',
    'src/assistx/contracts/schemas/repository_source_binding.py': '664a85450e5b71797c50d7c36ed59fe22a61faee',
    'src/assistx/contracts/schemas/task_authority.py': 'b303fc7bd80328aa3dc3d96c5ac7ea2cbc315feb',
    'src/assistx/contracts/schemas/voice_auth_decision.py': 'a9d711adc7e8ede3f1f1f929f5578c78d5179238',
    'src/assistx/contracts/version.py': 'd73a7987368c27a0e90e9c2f9806f0ce8065c55a',
    'src/assistx/controller_runtime.py': 'ea306ba1121d101be8271c9624bc3f9187d02873',
    'src/assistx/durable_harvester.py': 'e20d90cd7bafe5730f98be6da8614c7bda82fe9e',
    'src/assistx/executor_security.py': '4cc5d1a5a6e0ef592a6fda86b92e70bd2f96574d',
    'src/assistx/fleet.py': '3222f3dcb20cf4dbe5a750d352fb666d5dee6c7d',
    'src/assistx/fleet_executor.py': '666608e282c0039b2779e0155a2968f27f7fc69e',
    'src/assistx/fleet_executor_compat.py': '91d99c7367ed0cebfc9739cfaea5c6b86e1927d7',
    'src/assistx/hermes_corpus_checkpoint.py': 'ff7091618919b2261b1cbf4df2602627afae8c1f',
    'src/assistx/hermes_corpus_custody.py': 'e504334468ae0962668f2abd37978532483ec29b',
    'src/assistx/hermes_high_water.py': 'ed8992eef765006cb6a12def4553b04d0129ecbe',
    'src/assistx/hermes_launch_gate.py': 'aef4baf09ffca725a2eea0033904672406ecf1bd',
    'src/assistx/improvement_cycle.py': '1b6637561d4a167fca561124fc84bf9e03a3847d',
    'src/assistx/improvement_runtime.py': '66298a332ac0b17db6a9cc3d340f33daf81b1b90',
    'src/assistx/kg_harvester.py': '22caac1624857f07e915d2e6af04087879e92409',
    'src/assistx/knowledge_roots.py': 'a7827f936f23a3e85103e26efa73e6c02d3a4124',
    'src/assistx/llm/__init__.py': '0336039b7f744bda581b12c2726413587c7385b3',
    'src/assistx/llm/client.py': '78f157c873c67465c380a0e17e57c3aaf418ea9a',
    'src/assistx/llm/output_integrity.py': 'a90d5ab76e7adf3289505f226017aa93ee3f2542',
    'src/assistx/llm_client.py': 'ed8d7f02d89c1ff187345e8c7bf7340281f64966',
    'src/assistx/neo4j_client.py': '4e1af8e0d2454cc48984e2422c0bd20273011aab',
    'src/assistx/outbox_client.py': '9b1b54d64bb65a870d1081adf8bda8ad0c1ac630',
    'src/assistx/owner_identity.py': '392fa5780d5ddd450f798281b711b99512b2e692',
    'src/assistx/repo_task_generator.py': 'b975e723f38c5d82ca7242711685ece854efce0e',
    'src/assistx/repository_path_policy.py': '9f0e8659dbf0611db688df5eccd751763f1c23fe',
    'src/assistx/repository_source_verifier.py': '6b2934ff9a64f3fef3a94e471aec5f4b3724f5ec',
    'src/assistx/runtime_projection.py': 'a14b9688ef789d8335371cd6e81722f5f9a3cc0f',
    'src/assistx/strict_claims.py': 'b50a86fb7fd989b40dff657f95c08d452efa8ea3',
    'src/assistx/strict_executor_adapter.py': '9c554adee1514085b7948a30d4bf08bca2de2711',
    'src/assistx/strict_executor_main.py': 'b50131407072ec9f60359dcfd643cdc8fa5d8fda',
    'src/assistx/swarm_memory.py': '1ff75acd591c5ddc18844a0378a0a1ca4a427ee9',
    'src/assistx/task_family_routing.py': 'fcd036120ce4421447605851ba1e5f288e115870',
    'src/assistx/work_supply.py': '4ac8b030963b89127327e5b9e27253ec9aabed72',
}


class Denied(ValueError):
    pass


def need(predicate: bool, reason: str) -> None:
    if not predicate:
        raise Denied(reason)


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\x00'+data).hexdigest()


def _module_file(source_root: Path, module: str) -> Path | None:
    if not module.startswith('assistx') or (module != 'assistx' and
                                              not module.startswith('assistx.')):
        return None
    parts = module.split('.')
    path = source_root / 'src'
    for part in parts:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', part):
            return None
        path = path / part
    regular = path.with_suffix('.py')
    package = path / '__init__.py'
    for candidate in (regular, package):
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def first_party_import_closure(source_root: Path = ROOT) -> set[str]:
    """AST-only first-party closure including all package initializer imports.

    Over-approximates if imports live in dead code; it cannot discover dynamic
    module names assembled at runtime, so it is not full execution provenance.
    """
    pending = deque(['assistx.strict_executor_main'])
    visited: set[str] = set()
    paths: set[str] = set()
    while pending:
        module = pending.popleft()
        if module in visited:
            continue
        visited.add(module)
        need(len(visited) <= 256, 'unbounded first-party import graph')
        path = _module_file(source_root, module)
        if path is None:
            continue
        need(path.stat().st_size <= 200000, 'unbounded Python module')
        paths.add(path.relative_to(source_root).as_posix())
        names = module.split('.')
        for index in range(1, len(names)):
            ancestor = '.'.join(names[:index])
            if _module_file(source_root, ancestor) is not None:
                pending.append(ancestor)
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeError) as exc:
            raise Denied('unparseable reviewed Python module') from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names_to_check = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    prefix = (module if path.name == '__init__.py'
                              else '.'.join(names[:-1]))
                    parts = prefix.split('.')
                    need(node.level <= len(parts), 'unresolvable relative import')
                    base = '.'.join(parts[:len(parts) - node.level + 1])
                    parent = base + ('.' + node.module if node.module else '')
                else:
                    parent = node.module or ''
                names_to_check = [parent] + [
                    parent + '.' + item.name for item in node.names]
            else:
                continue
            for imported in names_to_check:
                if _module_file(source_root, imported) is not None:
                    pending.append(imported)
    need('src/assistx/strict_executor_main.py' in paths,
         'strict executor entrypoint missing from closure')
    return paths


def require_complete_closure(closure: set[str], pinned_paths: set[str]) -> None:
    need(closure == pinned_paths,
         'unreviewed inherited or stale first-party import: '
         + ','.join(sorted(closure.symmetric_difference(pinned_paths))[:8]))


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
    closure_confirmed = source_root == ROOT
    if closure_confirmed:
        require_complete_closure(first_party_import_closure(source_root),
                                 set(BLOBS))
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
        'first_party_static_import_closure_complete':closure_confirmed,
        'dynamic_imports_and_inherited_binary_dependencies_verified':False,
        'package_initializer_side_effects_pinned':closure_confirmed,
        'reviewed_sources':observed,
        'candidate_entrypoint_sha256':ENTRYPOINT_SHA256,
        'current_legacy_root_entrypoint_is_not_candidate':True,
        'unreviewed_image_base_digest':BASE_IMAGE_ID,
        'research_recipe_base_image_id':RESEARCH_BASE_IMAGE_ID,
        'legacy_running_image_usable_as_replacement_parent':False,
        'independently_approved_runtime_base_selected':False,
        'base_os_and_toolchain_supply_chain_verified':False,
        'reproducible_image_binary_proven':False,
        'real_signing_key_or_production_attestations_included':False,
        'image_overlay_built_or_run':False,
        'production_release_authorized':False,
        'decision':'HOLD',
    }


def dockerfile_recipe(*, source_root: Path = ROOT) -> str:
    """String recipe only. Caller MUST NOT treat it as approved image build."""
    inventory = source_inventory(source_root)
    need(inventory['reviewed_hardened_source_count']==len(BLOBS),
         'incomplete signed-source candidate context')
    need(RESEARCH_BASE_IMAGE_ID == 'sha256:' + '0' * 64 and
         BASE_IMAGE_ID != RESEARCH_BASE_IMAGE_ID,
         'production or unapproved runtime base cannot back research recipe')
    lines = [
        '# STATIC RESEARCH RECIPE: DO NOT BUILD; base digest is NONEXISTENT',
        '# No signed runtime base has independent custody approval',
        'FROM ' + RESEARCH_BASE_IMAGE_ID,
    ]
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
