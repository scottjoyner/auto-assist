"""Synthetic two-signer Ed25519/chain witness negative suite; no real keys or NAS."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/hermes-rootless"))
import verify_signed_curated_knowledge as gate

class SignedCuratedKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="og-hermes-knowledge-",dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.root.chmod(0o700)
        (self.root/"memo.md").write_bytes(b"SYNTHETIC ONLY - protected evidence excluded\n")
        payload=(self.root/"memo.md").read_bytes()
        manifest={
            "schema":1, "kind":"synthetic-hermes-curated-v1",
            "generation":"synthetic-20261021",
            "files":[{"path":"memo.md","size":len(payload),
                      "sha256":hashlib.sha256(payload).hexdigest()}],
        }
        self.producer=Ed25519PrivateKey.generate()
        self.reviewer=Ed25519PrivateKey.generate()
        pub=lambda key:key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        self.trust={"producer":pub(self.producer),"reviewer":pub(self.reviewer)}
        self.checkpoint={"schema":1,"sequence":0,"head_sha256":"0"*64}
        self.body={"schema":1,"domain":gate.DOMAIN,"sequence":1,
                   "previous_sha256":"0"*64,"manifest":manifest}
        self.envelope=self.sign(self.body)

    def sign(self, body, *, producer=None, reviewer=None):
        data=b"assistx-hermes-synthetic\0"+gate.canonical(body)
        out=copy.deepcopy(body)
        out["signatures"]={
            "producer":(producer or self.producer).sign(data).hex(),
            "reviewer":(reviewer or self.reviewer).sign(data).hex(),
        }
        return out

    def check(self, env=None,trust=None,checkpoint=None,root=None):
        return gate.validate(self.root if root is None else root,
                             self.envelope if env is None else env,
                             self.trust if trust is None else trust,
                             self.checkpoint if checkpoint is None else checkpoint)
    def deny(self,result,code=None):
        self.assertFalse(result["synthetic_corpus_verified"],result)
        self.assertIsNone(result["new_synthetic_head_sha256"])
        self.assertFalse(result["production_deployment_authorized"])
        self.assertEqual(result["decision"],"HOLD")
        if code:self.assertIn(code,result["violations"])

    def test_two_distinct_signatures_and_corpus_match(self):
        r=self.check()
        self.assertTrue(r["synthetic_signatures_valid"],r)
        self.assertTrue(r["synthetic_checkpoint_link_valid"])
        self.assertTrue(r["synthetic_corpus_verified"])
        self.assertRegex(r["new_synthetic_head_sha256"],r"^[0-9a-f]{64}$")
        self.assertFalse(r["independent_key_custody_verified"])
        self.assertFalse(r["rollback_resistant_external_checkpoint"])
        self.assertFalse(r["production_knowledge_mount_authorized"])
        self.assertEqual(r["decision"],"HOLD")

    def test_default_never_opens_files(self):
        with patch.object(gate.corpus,"walk_synthetic") as io:
            r=gate.validate()
        io.assert_not_called()
        self.deny(r,"signed_synthetic_inputs_missing")

    def test_tampered_manifest_never_scans(self):
        e=copy.deepcopy(self.envelope)
        e["manifest"]["files"][0]["size"]+=1
        with patch.object(gate.corpus,"walk_synthetic") as scan:
            r=self.check(env=e)
        scan.assert_not_called()
        self.deny(r,"ed25519_signature_invalid")

    def test_wrong_signature_and_swapped_signers(self):
        bad=copy.deepcopy(self.envelope)
        bad["signatures"]["reviewer"]=self.envelope["signatures"]["producer"]
        self.deny(self.check(env=bad),"ed25519_signature_invalid")
        bad=copy.deepcopy(self.envelope)
        bad["signatures"]["producer"]="ab"*64
        self.deny(self.check(env=bad),"ed25519_signature_invalid")

    def test_single_signer_cannot_claim_two_roles(self):
        trust=dict(self.trust)
        trust["reviewer"]=trust["producer"]
        self.deny(self.check(trust=trust),"signers_not_distinct")
        e=self.sign(self.body,reviewer=self.producer)
        self.deny(self.check(env=e),"ed25519_signature_invalid")

    def test_signature_valid_but_wrong_generation_denied(self):
        for value in ("production-20261021","synthetic","synthetic-ABCDEF"):
            body=copy.deepcopy(self.body)
            body["manifest"]["generation"]=value
            self.deny(self.check(env=self.sign(body)))

    def test_domain_separation_enforced(self):
        body=copy.deepcopy(self.body)
        body["domain"]="assistx.hermes.production.v1"
        self.deny(self.check(env=self.sign(body)),"wrong_signature_domain")

    def test_stale_replay_and_skipped_sequence_denied(self):
        self.deny(self.check(checkpoint={"schema":1,"sequence":1,
                          "head_sha256":"a"*64}),"stale_or_skipped_sequence")
        body=copy.deepcopy(self.body)
        body["sequence"]=3
        self.deny(self.check(env=self.sign(body)),"stale_or_skipped_sequence")

    def test_rewound_chain_head_denied(self):
        body=copy.deepcopy(self.body)
        body["sequence"]=2
        body["previous_sha256"]="0"*64
        self.deny(self.check(env=self.sign(body),checkpoint={
            "schema":1,"sequence":1,"head_sha256":"a"*64}),"chain_head_mismatch")

    def test_valid_next_generation_with_explicit_checkpoint(self):
        prev=self.check()
        self.assertTrue(prev["synthetic_corpus_verified"])
        second=copy.deepcopy(self.body)
        second["sequence"]=2
        second["previous_sha256"]=prev["new_synthetic_head_sha256"]
        r=self.check(env=self.sign(second),checkpoint={
            "schema":1,"sequence":1,"head_sha256":prev["new_synthetic_head_sha256"]})
        self.assertTrue(r["synthetic_corpus_verified"])
        self.assertFalse(r["production_deployment_authorized"])

    def test_fake_or_incomplete_trust_rejected(self):
        for trust in ({"producer":self.trust["producer"]},
                      {"producer":"not-hex","reviewer":self.trust["reviewer"]},
                      {"producer":self.trust["producer"],"reviewer":None}):
            with self.subTest(trust=str(trust)[:25]):
                self.deny(self.check(trust=trust))

    def test_checkpoint_schema_and_genesis_denied(self):
        for cp in ({"schema":1,"sequence":0,"head_sha256":"f"*64},
                   {"schema":1,"sequence":True,"head_sha256":"0"*64},
                   {"schema":1,"sequence":0,"head_sha256":"0"*64,"trusted":True}):
            with self.subTest(checkpoint=str(cp)[:22]):
                self.deny(self.check(checkpoint=cp))

    def test_nas_paths_rejected_even_with_valid_signatures(self):
        for path in ("/nas","/media/scott/NAS5","/home/scott/git"):
            with self.subTest(path=path):
                self.deny(self.check(root=path),"synthetic_corpus_rejected")

    def test_modified_synthetic_content_rejected_after_authentic_signatures(self):
        (self.root/"memo.md").write_text("different synthetic bytes")
        r=self.check()
        self.assertTrue(r["synthetic_signatures_valid"])
        self.deny(r,"synthetic_corpus_rejected")

    def test_extra_unapproved_signed_fields_denied(self):
        e=copy.deepcopy(self.envelope)
        e["production_deployment_authorized"]=True
        self.deny(self.check(env=e),"signed_envelope_schema")

    def test_output_never_exposes_secret_or_host_paths(self):
        s=json.dumps(self.check(),sort_keys=True)
        for prohibited in ("/home/scott", "/nas", str(self.root),
                           "PRIVATE_KEY", self.trust["producer"]):
            self.assertNotIn(prohibited,s)

if __name__=="__main__":
    unittest.main()
