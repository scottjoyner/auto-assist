"""Independent pin-bound signing evidence must never imply model execution."""
from __future__ import annotations

import copy
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from assistx import runtime_projection_v2 as signing
from assistx.fleet_link_signature_witness import verify_pinned_projection_signature
from tests.test_fleet_link_embedding_preview import approved_projection,install


def signed_pair():
    projection=approved_projection()
    private_key=Ed25519PrivateKey.generate()
    projection["signature"]=signing.projection_signature(projection,private_key)
    pinned=private_key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
    return projection,pinned


def test_independently_pinned_signature_verify_and_stays_observation_only(monkeypatch):
    document,pinned=signed_pair()
    assert verify_pinned_projection_signature(document,pinned) is True
    client,calls=install(monkeypatch,projection=document)
    monkeypatch.setenv("ASSISTX_RUNTIME_PROJECTION_VERIFY_PUBLIC_KEY_HEX",pinned)
    result=client.get("/api/fleet-link/embeddings/preview")
    assert result.status_code==200
    info=result.json()
    assert info["projection_ed25519_pin_verified"] is True
    assert info["matched_embedding_count"]==1
    assert info["execution_authorized"] is False
    assert info["artifact_identity_independently_proved"] is False
    assert info["sidecar_identity_cryptographically_verified"] is False
    assert all(method=="GET" for method,_ in calls)
    assert pinned not in json.dumps(info)


def test_without_pin_stays_explicitly_unverified(monkeypatch):
    document,pinned=signed_pair()
    client,calls=install(monkeypatch,projection=document)
    monkeypatch.delenv("ASSISTX_RUNTIME_PROJECTION_VERIFY_PUBLIC_KEY_HEX",raising=False)
    info=client.get("/api/fleet-link/embeddings/preview").json()
    assert info["matched_embedding_count"]==1
    assert info["projection_ed25519_pin_verified"] is False
    assert info["execution_authorized"] is False


def test_bad_pin_or_mismatched_signer_fails_closed_without_sidecar_get(monkeypatch):
    document,pinned=signed_pair()
    for other in ("garbage", "0"*64,
                  Ed25519PrivateKey.generate().public_key().public_bytes(
                      Encoding.Raw,PublicFormat.Raw).hex()):
        client,calls=install(monkeypatch,projection=document)
        monkeypatch.setenv("ASSISTX_RUNTIME_PROJECTION_VERIFY_PUBLIC_KEY_HEX",other)
        result=client.get("/api/fleet-link/embeddings/preview")
        info=result.json()
        assert info["mode"]=="NOT_VERIFIED"
        assert info["matched_embedding_count"]==0
        assert info["projection_ed25519_pin_verified"] is False
        assert calls==[]


def test_mutation_cannot_ride_checksum_shape_or_stale_signature(monkeypatch):
    document,pinned=signed_pair()
    tampered=copy.deepcopy(document)
    tampered["providers"][0]["models"][0]["artifact_fingerprint"]="sha256:"+"b"*64
    from assistx import runtime_projection as legacy
    tampered["checksum"]=legacy.projection_checksum(tampered)
    # Signature is still that of the originally approved artifact.
    assert verify_pinned_projection_signature(tampered,pinned) is False
    client,calls=install(monkeypatch,projection=tampered)
    monkeypatch.setenv("ASSISTX_RUNTIME_PROJECTION_VERIFY_PUBLIC_KEY_HEX",pinned)
    info=client.get("/api/fleet-link/embeddings/preview").json()
    assert info["mode"]=="NOT_VERIFIED"
    assert calls==[]


def test_signature_structure_can_be_plausible_but_not_verified(monkeypatch):
    document,pinned=signed_pair()
    altered=copy.deepcopy(document)
    altered["signature"]=("A" if document["signature"][0]!="A" else "B")+document["signature"][1:]
    assert verify_pinned_projection_signature(altered,pinned) is False
    client,calls=install(monkeypatch,projection=altered)
    monkeypatch.setenv("ASSISTX_RUNTIME_PROJECTION_VERIFY_PUBLIC_KEY_HEX",pinned)
    assert client.get("/api/fleet-link/embeddings/preview").json()["matched_embedding_count"]==0
    assert calls==[]


def test_wrong_schema_or_key_never_elevates_proof(monkeypatch):
    document,pinned=signed_pair()
    for key,value in (("schema_version","1"),("signature_algorithm","HMAC"),
                      ("source","other-source"),("signature",None)):
        altered={**document,key:value}
        assert verify_pinned_projection_signature(altered,pinned) is False


def test_operator_auth_still_required_with_valid_pinned_signature(monkeypatch):
    document,pinned=signed_pair()
    client,calls=install(monkeypatch,projection=document,auth=False)
    monkeypatch.setenv("ASSISTX_RUNTIME_PROJECTION_VERIFY_PUBLIC_KEY_HEX",pinned)
    assert client.get("/api/fleet-link/embeddings/preview").status_code==401
    assert calls==[]
