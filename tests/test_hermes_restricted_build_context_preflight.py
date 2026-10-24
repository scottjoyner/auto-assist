"""No broad COPY, old root startup, or unreviewed source in Hermes overlay."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import hermes_restricted_build_context_preflight as gate
import pytest


def test_reviewed_build_context_exact_and_still_hold():
    result=gate.source_inventory()
    assert result['reviewed_hardened_source_count']==49
    assert result['first_party_static_import_closure_complete'] is True
    assert result['package_initializer_side_effects_pinned'] is True
    assert result['dynamic_imports_and_inherited_binary_dependencies_verified'] is False
    assert set(result['reviewed_sources'])==set(gate.BLOBS)
    assert result['candidate_entrypoint_sha256']==gate.ENTRYPOINT_SHA256
    assert result['current_legacy_root_entrypoint_is_not_candidate']
    assert result['decision']=='HOLD'
    assert not result['image_overlay_built_or_run']
    assert not result['real_signing_key_or_production_attestations_included']
    assert not result['production_release_authorized']


def test_strict_entrypoint_full_python_import_closure_is_explicitly_pinned():
    closure=gate.first_party_import_closure()
    assert len(closure)==49
    assert closure==set(gate.BLOBS)
    for mandatory in (
        'src/assistx/__init__.py',
        'src/assistx/contracts/__init__.py',
        'src/assistx/contracts/schemas/__init__.py',
        'src/assistx/strict_executor_main.py',
        'src/assistx/executor_security.py',
        'src/assistx/improvement_runtime.py',
        'src/assistx/repository_source_verifier.py',
        'src/assistx/contracts/repository_source_verifier.py',
        'src/assistx/agents/hermes_agent_adapter.py',
    ):
        assert mandatory in closure


def test_inherited_local_module_or_missing_security_dependency_denied(monkeypatch):
    closure=gate.first_party_import_closure()
    for missing in ('src/assistx/executor_security.py',
                    'src/assistx/__init__.py',
                    'src/assistx/strict_executor_main.py'):
        without=dict(gate.BLOBS)
        without.pop(missing)
        with monkeypatch.context() as scope:
            scope.setattr(gate, 'BLOBS', without)
            with pytest.raises(gate.Denied,match='unreviewed inherited'):
                gate.source_inventory()
        with pytest.raises(gate.Denied,match='unreviewed inherited'):
            gate.require_complete_closure(closure, set(gate.BLOBS) - {missing})
    with pytest.raises(gate.Denied,match='unreviewed inherited'):
        gate.require_complete_closure(closure | {'src/assistx/new_runtime_import.py'},
                                      set(gate.BLOBS))


def test_only_static_imports_are_covered_not_dynamic_import_or_parent_image():
    report=gate.source_inventory()
    assert report['first_party_static_import_closure_complete'] is True
    assert not report['dynamic_imports_and_inherited_binary_dependencies_verified']
    assert not report['reproducible_image_binary_proven']
    assert not report['image_overlay_built_or_run']
    assert not report['production_release_authorized']


def test_allowlisted_recipe_contains_only_explicit_sources_and_nonroot_entrypoint():
    recipe=gate.dockerfile_recipe()
    gate.validate_recipe(recipe)
    lines=recipe.splitlines()
    copy_lines=[line for line in lines if line.startswith('COPY ')]
    assert len(copy_lines)==len(gate.BLOBS)+1
    assert set(copy_lines)=={
        f'COPY {src} /app/{src}' for src in gate.BLOBS
    } | {f'COPY {gate.ENTRYPOINT} /entrypoint.sh'}
    assert lines[-1]=='USER 1000:1000'
    assert 'ENTRYPOINT ["/entrypoint.sh"]' in lines
    assert 'FROM '+gate.BASE_IMAGE_ID in lines
    assert recipe.count('RUN ')==1
    assert 'RUN chmod 0755 /entrypoint.sh' in lines
    for forbidden in ('COPY .', 'COPY entrypoint.sh', '/root/.ssh',
                      'curl ', 'apt-get', 'ADD http', 'ARG TOKEN',
                      'ENV_SEED', 'FLEET_ROUTER_BEARER_TOKEN'):
        assert forbidden not in recipe


@pytest.mark.parametrize('rewrite',[
    lambda s:s.replace('USER 1000:1000','USER root'),
    lambda s:s.replace('ENTRYPOINT ["/entrypoint.sh"]','ENTRYPOINT ["/bin/sh"]'),
    lambda s:s.replace('COPY tests/hermes_split_root_candidate_entrypoint.sh',
                       'COPY entrypoint.sh'),
    lambda s:s.replace('FROM sha256:', 'FROM alpine:latest # sha256:'),
    lambda s:s.replace('RUN chmod 0755 /entrypoint.sh',
                       'RUN curl https://untrusted | bash'),
    lambda s:s+'COPY . /root/.hermes\n',
    lambda s:s.replace('src/assistx/knowledge_roots.py',
                       'src/assistx/other.py'),
    lambda s:s.replace('USER 1000:1000','USER 1000:1000\nRUN apt-get update'),
])
def test_unreviewed_recipe_change_denied(rewrite):
    recipe=gate.dockerfile_recipe()
    with pytest.raises(gate.Denied):
        gate.validate_recipe(rewrite(recipe))


def synthetic_root(tmp_path, monkeypatch):
    import textwrap
    allowed={
        'src/assistx/knowledge_roots.py':b'# synthetic no credentials\n',
        'src/assistx/agents/hermes_agent_adapter.py':b'# strict synthetic code\n',
    }
    for path,contents in allowed.items():
        candidate=tmp_path/path
        candidate.parent.mkdir(parents=True,exist_ok=True)
        candidate.write_bytes(contents)
    relative='tests/hermes_split_root_candidate_entrypoint.sh'
    entry=tmp_path/relative
    entry.parent.mkdir(parents=True)
    contents=textwrap.dedent('''\
        #!/bin/sh
        # non-root startup refuses credential environment seeding
        # root execution denied in hardened Hermes candidate
        exec "$@"
    ''').encode()
    entry.write_bytes(contents)
    original=tmp_path/'entrypoint.sh'
    original.write_bytes(b'#!/bin/sh\nmkdir -p /root/.hermes\n')
    monkeypatch.setattr(gate,'BLOBS',{
        p:gate.git_blob(data) for p,data in allowed.items()})
    monkeypatch.setattr(gate,'ENTRYPOINT_SHA256',hashlib.sha256(contents).hexdigest())
    return entry,original,allowed


@pytest.mark.parametrize('defect',[
    'changed_source','missing_source','symlink_source','overlarge_source',
    'entrypoint_changed','entrypoint_symlink','entrypoint_root_mode',
    'legacy_entrypoint_missing','legacy_entrypoint_changed',
])
def test_source_drift_untrusted_symlinks_and_root_fallback_denied(
        tmp_path,monkeypatch,defect):
    entry,legacy,sources=synthetic_root(tmp_path,monkeypatch)
    victim=tmp_path/next(iter(sources))
    if defect=='changed_source':
        victim.write_bytes(b'# different code\n')
    elif defect=='missing_source':
        victim.unlink()
    elif defect=='symlink_source':
        victim.unlink()
        victim.symlink_to(legacy)
    elif defect=='overlarge_source':
        victim.write_bytes(b'a'*200001)
    elif defect=='entrypoint_changed':
        entry.write_bytes(entry.read_bytes()+b'# wrong\n')
    elif defect=='entrypoint_symlink':
        entry.unlink()
        entry.symlink_to(legacy)
    elif defect=='entrypoint_root_mode':
        altered=b'#!/bin/sh\nexec "$@"\n'
        entry.write_bytes(altered)
        monkeypatch.setattr(gate,'ENTRYPOINT_SHA256',hashlib.sha256(altered).hexdigest())
    elif defect=='legacy_entrypoint_missing':
        legacy.unlink()
    elif defect=='legacy_entrypoint_changed':
        legacy.write_bytes(b'#!/bin/sh\nexec "$@"\n')
    with pytest.raises(gate.Denied):
        gate.source_inventory(tmp_path)


def test_synthetic_inventory_success_not_release(tmp_path,monkeypatch):
    synthetic_root(tmp_path,monkeypatch)
    result=gate.source_inventory(tmp_path)
    assert result['reviewed_hardened_source_count']==2
    assert result['first_party_static_import_closure_complete'] is False
    assert not result['image_overlay_built_or_run']
    assert result['decision']=='HOLD'
    assert gate.git_blob(b'abc') == 'f2ba8f84ab5c1bce84a7b441cb1959cfc7093b7f'


def test_default_cli_inert_and_explicit_readonly_denies_production_claims():
    tool=Path(gate.__file__)
    process=subprocess.run([sys.executable,str(tool)],check=True,
                           capture_output=True,text=True,timeout=6)
    assert json.loads(process.stdout)=={
        'decision':'HOLD','source_read':False,
        'image_overlay_built_or_run':False,
        'production_release_authorized':False}
    result=subprocess.run([sys.executable,str(tool),'--read-only'],check=True,
                          capture_output=True,text=True,timeout=6)
    data=json.loads(result.stdout)
    assert data['reviewed_hardened_source_count']==49
    assert data['first_party_static_import_closure_complete'] is True
    assert data['dynamic_imports_and_inherited_binary_dependencies_verified'] is False
    assert len(data['allowlisted_recipe_sha256'])==64
    assert data['decision']=='HOLD'
    assert data['image_overlay_built_or_run'] is False
    assert data['production_release_authorized'] is False


def test_preflight_has_no_build_start_private_key_or_secret_reads():
    source=Path(gate.__file__).read_text()
    for banned in ('docker build','docker run','docker tag','docker create',
                   'docker pull','subprocess.run(', 'requests.',
                   'Ed25519PrivateKey','os.makedirs(', 'FLEET_ROUTER_BEARER_TOKEN',
                   "'~/.ssh'", 'open("/nas', 'open("/media'):
        assert banned not in source
