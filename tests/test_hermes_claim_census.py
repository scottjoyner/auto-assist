"""Synthetic >500-record census, fail-closed churn and missing-claim tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

SRC=Path(__file__).resolve().parents[1]/"src/assistx/hermes_claim_census.py"
spec=importlib.util.spec_from_file_location("hermes_census_isolated",SRC)
c=importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def task(i):
    return {
        "task_id":f"synthetic-{i:05d}","claimed_by":"hermes-local",
        "target_agent_id":"hermes-local","claim_id":f"claim-{i:05d}",
        "session_id":"session-1","attempt":1,
        "lease_expiry":123456789,"status":"CLAIMED",
    }


class FakeSession:
    def __init__(self, scans):
        self.scans=scans
        self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def run(self,query,params):
        self.calls.append((query,params))
        assert "LIMIT $limit" in query
        assert "t.claimed_by=$agent OR t.target_agent_id=$agent" in query
        return iter(self.scans.pop(0)[:params["limit"]])


class CensusFixtureTests(unittest.TestCase):
    def get(self, scans, limit=10000):
        session=FakeSession(scans)
        result=c.candidate_readonly(lambda:session,max_rows=limit)
        return result,session

    def test_synthetic_621_claims_beyond_500_are_included(self):
        rows=[task(i) for i in range(621)]
        result,s=self.get([rows,rows])
        self.assertTrue(result["bounded_double_scan_stable"],result)
        self.assertEqual(result["candidate_count"],621)
        self.assertEqual(len(s.calls),2)
        self.assertFalse(result["authoritative_complete_census"])
        self.assertEqual(result["decision"],"HOLD")

    def test_missing_claim_on_active_task_is_denied_not_omitted(self):
        rows=[task(1),task(2)]
        rows[1]["claim_id"]=None
        out,_=self.get([rows,rows])
        self.assertFalse(out["bounded_double_scan_stable"])
        self.assertEqual(out["reason"],"incomplete_or_unqualified_source_claim_inventory")

    def test_assigned_task_wrong_claim_owner_denied(self):
        rows=[task(1)]
        rows[0]["claimed_by"]="someone-else"
        out,_=self.get([rows,rows])
        self.assertFalse(out["bounded_double_scan_stable"])

    def test_unrecognized_prefixed_hermes_owner_invalidates_exact_owner_census(self):
        row = task(3)
        row["claimed_by"] = "hermes-shadow"
        row["target_agent_id"] = "hermes-shadow"
        out, session = self.get([[row], [row]])
        self.assertFalse(out["bounded_double_scan_stable"])
        self.assertEqual(out["reason"], "incomplete_or_unqualified_source_claim_inventory")
        self.assertIn("STARTS WITH 'hermes-'", session.calls[0][0])
        self.assertEqual(out["decision"], "HOLD")

    def test_duplicate_task_rejected(self):
        rows=[task(1),task(1)]
        out,_=self.get([rows,rows])
        self.assertFalse(out["bounded_double_scan_stable"])

    def test_unsorted_task_rejected(self):
        rows=[task(2),task(1)]
        out,_=self.get([rows,rows])
        self.assertFalse(out["bounded_double_scan_stable"])

    def test_unsafe_zero_attempt_rejected(self):
        row=task(1);row["attempt"]=0
        out,_=self.get([[row],[row]])
        self.assertFalse(out["bounded_double_scan_stable"])

    def test_unsafe_missing_session_denied(self):
        row=task(1);row["session_id"]=None
        out,_=self.get([[row],[row]])
        self.assertFalse(out["bounded_double_scan_stable"])

    def test_changed_claim_between_scans_rejected(self):
        old=task(1);new=dict(old,claim_id="claim-new")
        out,_=self.get([[old],[new]])
        self.assertEqual(out["reason"],"source_changed_between_read_committed_scans")
        self.assertIsNone(out["candidate_digest_sha256"])

    def test_added_claim_during_census_rejected(self):
        out,_=self.get([[task(1)],[task(1),task(2)]])
        self.assertFalse(out["bounded_double_scan_stable"])

    def test_removed_claim_during_census_rejected(self):
        out,_=self.get([[task(1),task(2)],[task(2)]])
        self.assertFalse(out["bounded_double_scan_stable"])

    def test_overflow_fails_closed(self):
        rows=[task(i) for i in range(6)]
        out,_=self.get([rows,rows],limit=5)
        self.assertEqual(out["reason"],"incomplete_or_unqualified_source_claim_inventory")
        self.assertIsNone(out["candidate_count"])

    def test_empty_double_scan_cannot_authorize_production(self):
        out,_=self.get([[],[]])
        self.assertTrue(out["bounded_double_scan_stable"])
        self.assertEqual(out["candidate_count"],0)
        self.assertFalse(out["authoritative_complete_census"])
        self.assertFalse(out["production_dispatch_authorized"])

    def test_invalid_bounds_denied_before_database_io(self):
        for val in (-1,0,True,10001,"500",None):
            with self.subTest(val=val):
                out,s=self.get([],limit=val)
                self.assertFalse(out["bounded_double_scan_stable"])
                self.assertEqual(s.calls,[])

    def test_unsafe_claim_identifier_denied(self):
        row=task(1);row["claim_id"]="../outside"
        out,_=self.get([[row],[row]])
        self.assertFalse(out["bounded_double_scan_stable"])

    def test_census_does_not_echo_claims_or_task_payloads(self):
        row=task(1)
        out,_=self.get([[row],[row]])
        self.assertNotIn(row["claim_id"],str(out))
        self.assertNotIn(row["task_id"],str(out))
        self.assertIsNotNone(out["candidate_digest_sha256"])

    def test_driver_outage_denies_without_leaking_db_error(self):
        def failed():
            raise RuntimeError("test-only synthetic connection failure")
        out=c.candidate_readonly(failed)
        self.assertFalse(out["bounded_double_scan_stable"])
        self.assertEqual(out["reason"],"incomplete_or_unqualified_source_claim_inventory")
        self.assertNotIn("synthetic connection failure",str(out))

    def test_synthetic_rollback_aba_is_not_proven_safe(self):
        row=task(1)
        out,_=self.get([[row],[row]])
        self.assertTrue(out["bounded_double_scan_stable"])
        self.assertFalse(out["source_generation_fenced"])
        self.assertFalse(out["independently_retained_high_water"])

if __name__=="__main__":
    unittest.main()
