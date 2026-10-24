"""CI image evidence is a bounded input, never a Hermes release credential."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import hermes_runtime_image_evidence_gate as gate
import pytest

HEAD = '17322b08d1c9e79bf1f051417f7a9d072651330f'
SOURCE = '558a2228563222d8475335ed9356b974a71ccb5b'
IMAGE = 'sha256:c951cffb31b48befeee0fabd95503baf36a6966f1671f8ea74e7379f922d6968'


def data():
    return {'source-sha.txt': (SOURCE+'\n').encode(),
            'image-id.txt': (IMAGE+'\n').encode(),
            'assistx-freeze.txt': b'package==1.0\n',
            'hermes-freeze.txt': b'hermes-agent==0.1\n'}


def artifact(contents=None):
    buf=BytesIO()
    with ZipFile(buf,'w',compression=ZIP_DEFLATED) as z:
        for name,blob in (data() if contents is None else contents).items():
            z.writestr(name,blob)
    return buf.getvalue()


def classify(blob=None, **overrides):
    raw=artifact() if blob is None else blob
    kw=dict(pinned_zip_sha256=hashlib.sha256(raw).hexdigest(),
            pr_head_sha=HEAD,workflow_success=True)
    kw.update(overrides)
    return gate.classify_artifact(raw,**kw)


def test_ci_evidence_is_verified_but_explicitly_not_a_release():
    r=classify()
    assert r['decision']=='HOLD'
    assert r['ci_build_artifact_sha256_verified'] is True
    assert r['ci_image_id']==IMAGE
    assert r['ci_recorded_source_sha']==SOURCE
    assert r['branch_head_sha']==HEAD
    assert r['artifact_source_equals_branch_head'] is False
    assert r['source_identity_requires_merge_checkout_reconciliation'] is True
    for claim in ('ci_image_proven_to_be_hermes_replacement',
                  'independent_image_signature_verified',
                  'owner_approved_input_and_output_custody',
                  'host_and_container_mount_admission_verified',
                  'network_loss_and_restart_persistence_verified',
                  'release_authorized'):
        assert r[claim] is False


def test_source_matching_branch_still_does_not_grant_release():
    r=classify(pr_head_sha=SOURCE)
    assert r['artifact_source_equals_branch_head'] is True
    assert r['source_identity_requires_merge_checkout_reconciliation'] is False
    assert r['release_authorized'] is False
    assert r['independent_image_signature_verified'] is False


@pytest.mark.parametrize('kwargs',[
    {'pinned_zip_sha256':'0'*64},
    {'pinned_zip_sha256':'not-sha256'},
    {'pr_head_sha':'0'*39},
    {'pr_head_sha':'untrusted-branch'},
    {'workflow_success':False},
    {'workflow_success':'success'},
])
def test_missing_wrong_digest_or_workflow_denied(kwargs):
    with pytest.raises(gate.UnsafeEvidence):
        classify(**kwargs)


@pytest.mark.parametrize('mutation',[
    'missing_image','missing_source','missing_freeze','extra',
    'traversal','duplicate','too_large','empty_inventory',
    'invalid_source','invalid_image','nonhex_image','binary_text',
    'symlink','invalid_encoding',
])
def test_unreviewed_or_malformed_zip_member_denied(mutation):
    d=data()
    if mutation=='missing_image':
        del d['image-id.txt']
    elif mutation=='missing_source':
        del d['source-sha.txt']
    elif mutation=='missing_freeze':
        del d['hermes-freeze.txt']
    elif mutation=='extra':
        d['secret-token.txt']=b'do-not-accept'
    elif mutation=='traversal':
        d['../image-id.txt']=d.pop('image-id.txt')
    elif mutation=='duplicate':
        blob=artifact()
        buf=BytesIO()
        with ZipFile(buf,'w',compression=ZIP_DEFLATED) as z:
            for name,body in data().items():
                z.writestr(name,body)
            z.writestr('image-id.txt',d['image-id.txt'])
        blob=buf.getvalue()
        with pytest.raises(gate.UnsafeEvidence):
            classify(blob)
        return
    elif mutation=='too_large':
        d['hermes-freeze.txt']=b'x'*16500
    elif mutation=='empty_inventory':
        d['hermes-freeze.txt']=b'\n'
    elif mutation=='invalid_source':
        d['source-sha.txt']=b'not-40-hex\n'
    elif mutation=='invalid_image':
        d['image-id.txt']=b'latest\n'
    elif mutation=='nonhex_image':
        d['image-id.txt']=b'sha256:'+(b'g'*64)+b'\n'
    elif mutation=='binary_text':
        d['image-id.txt']+=b'\x00'
    elif mutation=='invalid_encoding':
        d['image-id.txt']=b'\xff'*64
    elif mutation=='symlink':
        buf=BytesIO()
        with ZipFile(buf,'w') as z:
            for name,body in d.items():
                if name=='image-id.txt':
                    info=ZipInfo(name)
                    info.create_system=3
                    info.external_attr=0o120777 << 16
                    z.writestr(info,body)
                else:
                    z.writestr(name,body)
        with pytest.raises(gate.UnsafeEvidence):
            classify(buf.getvalue())
        return
    with pytest.raises((gate.UnsafeEvidence,UnicodeError)):
        classify(artifact(d))


def test_tampered_or_overlarge_zip_denied():
    blob=artifact()
    with pytest.raises(gate.UnsafeEvidence):
        classify(blob[:-2]+b'XY',pinned_zip_sha256=hashlib.sha256(blob).hexdigest())
    with pytest.raises(gate.UnsafeEvidence):
        classify(b'X'*70000)
    with pytest.raises(gate.UnsafeEvidence):
        classify(b'no-zip')


def test_inert_command_creates_nothing_and_reports_hold():
    r=subprocess.run([sys.executable,str(Path(gate.__file__))],
                     capture_output=True,text=True,check=True,timeout=7)
    assert json.loads(r.stdout)=={
        'decision':'HOLD','artifact_read':False,'release_authorized':False}


def test_cli_verify_synthetic_fixture_does_not_promote(tmp_path):
    raw=artifact()
    p=tmp_path/'synthetic-runtime-image-evidence.zip'
    p.write_bytes(raw)
    args=['--read-artifact',str(p), '--artifact-sha256',hashlib.sha256(raw).hexdigest(),
          '--pr-head',HEAD,'--workflow-success']
    r=subprocess.run([sys.executable,str(Path(gate.__file__)),*args],
                     capture_output=True,text=True,check=True,timeout=7)
    report=json.loads(r.stdout)
    assert report['ci_build_artifact_sha256_verified'] is True
    assert report['release_authorized'] is False
    assert report['source_identity_requires_merge_checkout_reconciliation']
