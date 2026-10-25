"""Signed image claims are authentic only with independently pinned identity."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import hermes_signed_image_provenance_gate as gate
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SOURCE = 'a'*40
TREE = 'b'*40
ADAPTER = 'c'*40
IMAGE = 'sha256:'+'d'*64
COMPOSE = 'e'*64
ISSUER = 'synthetic-independent-builder'


def inputs(key):
    pub=key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return dict(externally_trusted_public_key=pub,
        pinned_public_key_sha256=hashlib.sha256(pub).hexdigest(),
        expected_issuer_id=ISSUER,
        expected_source_commit=SOURCE,
        expected_source_tree=TREE,
        expected_adapter_blob=ADAPTER,
        expected_image_id=IMAGE,
        expected_compose_sha256=COMPOSE,
        minimum_independently_recorded_generation=3)


def claims():
    return dict(schema=1,audience=gate.AUDIENCE,repository=gate.REPOSITORY,
                issuer_id=ISSUER,generation=3,source_commit=SOURCE,
                source_tree=TREE,adapter_blob=ADAPTER,image_id=IMAGE,
                restricted_compose_sha256=COMPOSE)


def signed(key, data=None):
    body=claims() if data is None else data
    sig=key.sign(gate.canonical(body)).hex()
    return gate.canonical({**body,'signature':sig})


def test_signed_synthetic_release_metadata_is_not_deployment_approval():
    signer=Ed25519PrivateKey.generate()
    r=gate.verify_signed_manifest(signed(signer),**inputs(signer))
    assert r['ed25519_signature_under_pinned_key_verified']
    assert r['expected_source_and_compose_match_manifest']
    assert r['release_generation_not_below_supplied_high_water']
    assert r['signed_image_id']==IMAGE
    assert r['decision']=='HOLD'
    for denial in ('independent_issuer_custody_and_revocation_verified',
                   'image_digest_physically_loaded_and_checked',
                   'image_binary_reproducibly_built_or_transparently_logged',
                   'owner_custody_approved_for_ro_and_rw_paths',
                   'real_container_mount_and_restart_tested',
                   'deployment_authorized'):
        assert r[denial] is False


def test_no_trust_on_first_use_and_forged_public_key_rejected():
    original=Ed25519PrivateKey.generate()
    attacker=Ed25519PrivateKey.generate()
    args=inputs(original)
    with pytest.raises(gate.Denied):
        gate.verify_signed_manifest(signed(attacker),**args)
    forged=inputs(attacker)
    forged['pinned_public_key_sha256']=args['pinned_public_key_sha256']
    with pytest.raises(gate.Denied):
        gate.verify_signed_manifest(signed(attacker),**forged)
    # Caller supplying all its own trust inputs may verify its own synthetic
    # signature; that alone is explicitly NEVER independent custody approval.
    assert gate.verify_signed_manifest(signed(attacker),**inputs(attacker))[
        'deployment_authorized'] is False


@pytest.mark.parametrize('field,bad',[
    ('schema',2),('schema',True),('audience','other-product'),
    ('repository','attacker/auto-assist'),('issuer_id','self-declared-key'),
    ('generation',2),('generation',True),('generation',2.5),
    ('source_commit','f'*40),('source_tree','0'*40),
    ('adapter_blob','0'*40),('image_id','sha256:'+'0'*64),
    ('restricted_compose_sha256','0'*64),
])
def test_even_correctly_signed_identity_drift_denied(field,bad):
    key=Ed25519PrivateKey.generate()
    doc=claims()
    doc[field]=bad
    with pytest.raises(gate.Denied):
        gate.verify_signed_manifest(signed(key,doc),**inputs(key))


@pytest.mark.parametrize('field,bad',[
    ('expected_source_commit','0'*40),('expected_source_tree','0'*40),
    ('expected_adapter_blob','0'*40),
    ('expected_image_id','sha256:'+'0'*64),
    ('expected_compose_sha256','0'*64),
    ('expected_issuer_id','unapproved-issuer'),
    ('minimum_independently_recorded_generation',4),
    ('minimum_independently_recorded_generation',True),
    ('minimum_independently_recorded_generation',0),
    ('externally_trusted_public_key',b'bad-key'),
    ('pinned_public_key_sha256','0'*64),
])
def test_independent_expected_identity_or_high_water_mismatch_denied(field,bad):
    key=Ed25519PrivateKey.generate()
    expected=inputs(key)
    expected[field]=bad
    with pytest.raises(gate.Denied):
        gate.verify_signed_manifest(signed(key),**expected)


def test_any_post_signature_content_change_is_denied():
    key=Ed25519PrivateKey.generate()
    body=json.loads(signed(key))
    for field,value in (('generation',4),('source_commit','0'*40),
                        ('image_id','sha256:'+'f'*64)):
        changed={**body,field:value}
        expected=inputs(key)
        if field=='generation':
            # Increase should pass the semantic floor, then fail signature.
            with pytest.raises(gate.Denied,match='signature'):
                gate.verify_signed_manifest(gate.canonical(changed),**expected)
        else:
            with pytest.raises(gate.Denied):
                gate.verify_signed_manifest(gate.canonical(changed),**expected)


@pytest.mark.parametrize('raw',[
    b'',b'\x00',b'not-json',b'[]',b'{}',b'\xff',
    b'{"schema":1,"schema":1}', b'{"generation":NaN}',
    b'0'*5000,
])
def test_malformed_or_unbounded_manifest_denied(raw):
    key=Ed25519PrivateKey.generate()
    with pytest.raises(gate.Denied):
        gate.verify_signed_manifest(raw,**inputs(key))


def test_missing_additional_and_malformed_signature_fields_denied():
    key=Ed25519PrivateKey.generate()
    for mutate in ('missing_sig','added_trust_root','bad_sig','missing_source'):
        doc=json.loads(signed(key))
        if mutate=='missing_sig':
            del doc['signature']
        elif mutate=='added_trust_root':
            doc['issuer_public_key']='synthetic-self-approval'
        elif mutate=='bad_sig':
            doc['signature']='f'*128
        elif mutate=='missing_source':
            del doc['source_commit']
        with pytest.raises(gate.Denied):
            gate.verify_signed_manifest(gate.canonical(doc),**inputs(key))


def test_default_cli_inert_and_no_signer_or_docker_commands():
    script=Path(gate.__file__)
    r=subprocess.run([sys.executable,str(script)],capture_output=True,
                     text=True,check=True,timeout=7)
    assert json.loads(r.stdout)=={
        'decision':'HOLD','verification_executed':False,
        'deployment_authorized':False}
    source=script.read_text()
    for text in ('Ed25519PrivateKey', 'docker run', 'docker build',
                 'docker restart', 'subprocess.run', 'os.makedirs',
                 'network.connect', 'requests.post', 'sign('):
        assert text not in source


def test_manifest_cli_does_not_accept_unverified_trust_inputs(tmp_path):
    key=Ed25519PrivateKey.generate()
    path=tmp_path/'unapproved.json'
    path.write_bytes(signed(key))
    proc=subprocess.run([sys.executable,str(Path(gate.__file__)),
                         '--manifest',str(path)],capture_output=True,
                        text=True,timeout=7)
    assert proc.returncode != 0
    assert 'HOLD' in proc.stderr


def test_cli_checks_external_synthetic_key_manifest_and_stays_hold(tmp_path):
    key=Ed25519PrivateKey.generate()
    manifest=tmp_path/'synthetic-manifest.json'
    pub=tmp_path/'synthetic-pub.key'
    manifest.write_bytes(signed(key))
    req=inputs(key)
    pub.write_bytes(req['externally_trusted_public_key'])
    args=[sys.executable,str(Path(gate.__file__)),
          '--manifest',str(manifest),
          '--externally-trusted-public-key',str(pub),
          '--pinned-public-key-sha256',req['pinned_public_key_sha256'],
          '--expected-issuer-id',ISSUER,
          '--expected-source-commit',SOURCE,
          '--expected-source-tree',TREE,
          '--expected-adapter-blob',ADAPTER,
          '--expected-image-id',IMAGE,
          '--expected-compose-sha256',COMPOSE,
          '--minimum-independently-recorded-generation','3']
    proc=subprocess.run(args,capture_output=True,text=True,timeout=7,check=True)
    result=json.loads(proc.stdout)
    assert result['ed25519_signature_under_pinned_key_verified']
    assert result['deployment_authorized'] is False
    assert result['decision']=='HOLD'
    pub_alias=tmp_path/'pub-symlink.key'
    pub_alias.symlink_to(pub)
    args[args.index(str(pub))]=str(pub_alias)
    blocked=subprocess.run(args,capture_output=True,text=True,timeout=7)
    assert blocked.returncode != 0 and 'HOLD' in blocked.stderr


def test_cli_never_has_a_private_key_or_signing_option():
    source=Path(gate.__file__).read_text()
    assert 'Ed25519PrivateKey' not in source
    assert '--private-key' not in source
    assert '--sign' not in source
    assert "'deployment_authorized': False" in source
