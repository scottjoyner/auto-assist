"""All synthetic-only collision rekey rejection tests, no Docker or Neo4j."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "endpoint_collision_rekey_synthetic_scenario.py"
spec = importlib.util.spec_from_file_location("isolated_rekey", SOURCE)
assert spec and spec.loader
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)

PFX = "scratch-acceptance-synthetic-test"

class NoTransaction:
    def run(self, *args, **kwargs):
        raise AssertionError("reject invalid/unapproved identity before any query")

class FakeResult:
    def __init__(self, data):
        self.data = data
    def __iter__(self):
        return iter(self.data)
    def single(self):
        return self.data[0] if self.data else None

class SyntheticTransaction:
    def __init__(self, *, owner=PFX, target_in_use=False, triple=False):
        self.owner=owner
        self.target_in_use=target_in_use
        self.triple=triple
        self.write_count=0
        self.labels=["ModelEndpoint", "ServiceEndpoint"]
        self.group=[
            {"ref":"ref-service", "labels":["ServiceEndpoint"],"service_type":"generic",
             "node_id":owner, "model_id":None,"owners":[owner],
             "serves_count":0,"exposes_count":1},
            {"ref":"ref-model","labels":self.labels,"service_type":"model_endpoint",
             "node_id":owner,"model_id":PFX+":old","owners":[owner],
             "serves_count":1,"exposes_count":1},
        ]
        if triple:self.group.append(dict(self.group[0],ref="ref-3"))
    def run(self, query, **params):
        if "OPTIONAL MATCH" in query:
            return FakeResult(self.group)
        if "RETURN count(e) AS n" in query and "SET e.endpoint_id" not in query:
            return FakeResult([{"n":int(self.target_in_use)}])
        if "SET e.endpoint_id" in query:
            self.write_count+=1
            return FakeResult([{"n":1}])
        raise AssertionError("unexpected query")

def rekey(tx, *, owner=PFX, approved=True, target=None):
    return subject.synthetic_rekey_in_transaction(
        tx, old_id=PFX+":old", new_id=target or PFX+":new",
        owner_id=owner, approved=approved,
    )

class SyntheticRekeyUnitTests(unittest.TestCase):
    def test_explicit_approval_required_before_queries(self):
        with self.assertRaises(subject.SyntheticRekeyHold):
            rekey(NoTransaction(),approved=False)

    def test_realish_identity_denied_before_queries(self):
        with self.assertRaises(subject.SyntheticRekeyHold):
            subject.synthetic_rekey_in_transaction(
                NoTransaction(),old_id="production-identity",
                new_id=PFX+":new",owner_id=PFX,approved=True)

    def test_same_source_and_target_rejected(self):
        with self.assertRaises(subject.SyntheticRekeyHold):
            subject.synthetic_rekey_in_transaction(
                NoTransaction(),old_id=PFX+":old",
                new_id=PFX+":old",owner_id=PFX,approved=True)

    def test_matching_pair_allows_only_one_synthetic_rekey(self):
        tx=SyntheticTransaction()
        rekey(tx)
        self.assertEqual(tx.write_count,1)

    def test_foreign_owner_rejected_without_write(self):
        tx=SyntheticTransaction()
        with self.assertRaises(subject.SyntheticRekeyHold):
            rekey(tx,owner=PFX+":foreign")
        self.assertEqual(tx.write_count,0)

    def test_duplicate_group_larger_than_two_rejected(self):
        tx=SyntheticTransaction(triple=True)
        with self.assertRaises(subject.SyntheticRekeyHold):rekey(tx)
        self.assertEqual(tx.write_count,0)

    def test_target_collision_rejected(self):
        tx=SyntheticTransaction(target_in_use=True)
        with self.assertRaises(subject.SyntheticRekeyHold):rekey(tx)
        self.assertEqual(tx.write_count,0)

    def test_missing_model_subtype_rejected(self):
        tx=SyntheticTransaction()
        tx.group[1]["labels"]=["ServiceEndpoint"]
        with self.assertRaises(subject.SyntheticRekeyHold):rekey(tx)
        self.assertEqual(tx.write_count,0)

    def test_conflicting_service_metadata_rejected(self):
        tx=SyntheticTransaction()
        tx.group[0]["service_type"]="model_endpoint"
        with self.assertRaises(subject.SyntheticRekeyHold):rekey(tx)
        self.assertEqual(tx.write_count,0)

    def test_model_relationship_missing_rejected(self):
        tx=SyntheticTransaction()
        tx.group[1]["serves_count"]=0
        with self.assertRaises(subject.SyntheticRekeyHold):rekey(tx)
        self.assertEqual(tx.write_count,0)

    def test_ownership_edge_missing_rejected(self):
        tx=SyntheticTransaction()
        tx.group[0]["exposes_count"]=0
        with self.assertRaises(subject.SyntheticRekeyHold):rekey(tx)
        self.assertEqual(tx.write_count,0)

    def test_stale_legacy_writer_negative_is_synthetic_and_explicit(self):
        text = SOURCE.read_text()
        self.assertIn(
            "stale_legacy_service_registration_corrupts_model_kind_even_after_uniqueness",
            text,
        )
        self.assertIn("canonical_writer_denies_legacy_semantic_corruption", text)
        self.assertIn("MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})", text)
        self.assertIn("SET e += $props", text)
        self.assertIn("endpoint_id=old", text)
        self.assertIn("service_type\": \"generic\"", text)
        self.assertNotIn("bolt://", text)
        self.assertNotIn("docker exec", text)

    def test_no_general_purpose_delete_or_deployment_entrance(self):
        text=SOURCE.read_text()
        self.assertNotIn("DETACH DELETE",text)
        self.assertNotIn("DELETE e",text)
        self.assertNotIn("docker run",text)
        self.assertNotIn("__main__",text)
        self.assertIn("scratch-acceptance-",text)

if __name__=="__main__":
    unittest.main()
