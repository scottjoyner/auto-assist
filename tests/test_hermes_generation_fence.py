"""Test-only generation lock protocol. No production API or database."""
from __future__ import annotations

import hashlib
from unittest.mock import Mock
import unittest

from assistx import hermes_generation_fence as g
from assistx import hermes_claim_census as legacy


def row(i=0):
    return {
        "task_id": "synthetic_%04d" % i,
        "claimed_by": "hermes-local",
        "target_agent_id": "hermes-local",
        "claim_id": "claim_%04d" % i,
        "session_id": "session_%04d" % i,
        "attempt": 1,
        "lease_expiry": 900000,
        "status": "CLAIMED",
    }


class Record:
    def __init__(self, generation): self.generation=generation
    def __getitem__(self, key):
        if key!="generation": raise KeyError(key)
        return self.generation


class Result:
    def __init__(self, data): self.data=data
    def single(self): return self.data
    def __iter__(self): return iter(self.data)


class Session:
    def __init__(self, rows, *, generation=7, missing=False):
        self.rows=rows
        self.generation=generation
        self.missing=missing
        self.queries=[]
        self.commits=0
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def begin_transaction(self):return self
    def commit(self):self.commits+=1
    def run(self, query, params):
        self.queries.append((query,params))
        if query==g.LOCK_AND_READ or query==g.VALIDATE_GENERATION:
            return Result(None if self.missing else Record(self.generation))
        if query==g.LOCK_AND_ADVANCE_WRITER:
            self.generation+=1
            return Result(Record(self.generation))
        if query==legacy.QUERY:
            return Result(self.rows)
        raise AssertionError("unexpected query")


class HermesGenerationFenceTests(unittest.TestCase):
    def test_exact_generation_locked_scan_has_no_release_authority(self):
        s=Session([row(1),row(2)])
        r=g.generation_locked_candidate(lambda:s)
        self.assertTrue(r["experiment_transaction_completed"])
        self.assertEqual(r["candidate_count"],2)
        self.assertEqual(r["candidate_generation"],7)
        self.assertEqual(s.commits,1)
        self.assertEqual([q for q,p in s.queries],
                         [g.LOCK_AND_READ,legacy.QUERY,g.VALIDATE_GENERATION])
        self.assertFalse(r["source_generation_fenced"])
        self.assertFalse(r["all_claim_writers_joined_generation_fence"])
        self.assertFalse(r["admin_rollback_protected"])
        self.assertFalse(r["production_dispatch_authorized"])
        self.assertEqual(r["decision"],"HOLD")
        self.assertEqual(set(r),{
            "schema","mode","owner","experiment_transaction_completed",
            "candidate_count","candidate_digest_sha256","candidate_generation",
            "reason","all_claim_writers_joined_generation_fence",
            "source_generation_fenced","outside_fleet_monotonic_head_verified",
            "admin_rollback_protected","source_owner_complete_census_verified",
            "production_dispatch_authorized","production_rollback_authorized","decision",
        })

    def test_out_of_order_and_duplicate_ids_fail_closed(self):
        for data in ([row(2),row(1)],[row(1),row(1)]):
            with self.subTest(test=len(data)):
                out=g.generation_locked_candidate(lambda:Session(data))
                self.assertFalse(out["experiment_transaction_completed"])
                self.assertIsNone(out["candidate_digest_sha256"])

    def test_missing_claim_or_wrong_owner_fails_closed(self):
        for field,value in [("claimed_by",None),("claim_id",None),
                            ("session_id",None),("attempt",0),
                            ("status","INVENTED")]:
            v=row()
            v[field]=value
            result=g.generation_locked_candidate(lambda:Session([v]))
            self.assertFalse(result["experiment_transaction_completed"])

    def test_generation_node_required_and_malformed_values_denied(self):
        for value in (None,True,-1,"3",3.1):
            with self.subTest(generation=value):
                out=g.generation_locked_candidate(
                    lambda:Session([row()],generation=value,
                                   missing=value is None))
                self.assertFalse(out["experiment_transaction_completed"])

    def test_missing_generation_gate_prevents_scan(self):
        s=Session([row()],missing=True)
        result=g.generation_locked_candidate(lambda:s)
        self.assertFalse(result["experiment_transaction_completed"])
        self.assertEqual(len(s.queries),1)
        self.assertIsNone(result["candidate_count"])

    def test_abort_when_generation_changes_during_scan(self):
        s=Session([row()])
        r=g.generation_locked_candidate(
            lambda:s,
            on_locked_for_testing=lambda: setattr(s,"generation",s.generation+1))
        self.assertFalse(r["experiment_transaction_completed"])
        self.assertEqual(s.commits,0)

    def test_opt_in_barrier_can_exercise_competing_writer(self):
        s=Session([row()],generation=11)
        values=[]
        out=g.generation_locked_candidate(
            lambda:s,on_locked_for_testing=lambda: values.append(s.generation))
        self.assertEqual(values,[11])
        self.assertTrue(out["experiment_transaction_completed"])

    def test_writer_lock_uses_same_generation_node(self):
        s=Session([])
        g.lock_writer_before_mutation(s)
        self.assertEqual(s.generation,8)
        self.assertEqual(s.queries[0][0],g.LOCK_AND_ADVANCE_WRITER)
        with self.assertRaises(ValueError):
            g.lock_writer_before_mutation(s,owner="other")
        self.assertEqual(len(s.queries),1)

    def test_bad_bounds_denied_without_queries(self):
        for bound in (None,False,0,-1,10001,"500",1.1):
            sess=Session([row()])
            out=g.generation_locked_candidate(lambda:sess,max_rows=bound)
            self.assertFalse(out["experiment_transaction_completed"])
            self.assertEqual(sess.queries,[])

    def test_excess_rows_denied_without_incomplete_result(self):
        s=Session([row(i) for i in range(3)])
        out=g.generation_locked_candidate(lambda:s,max_rows=2)
        self.assertFalse(out["experiment_transaction_completed"])
        self.assertIsNone(out["candidate_count"])

    def test_callback_exception_is_denied(self):
        def bad():
            raise RuntimeError("synthetic fail")
        out=g.generation_locked_candidate(lambda:Session([row()]),
                                          on_locked_for_testing=bad)
        self.assertFalse(out["experiment_transaction_completed"])
        self.assertEqual(out["decision"],"HOLD")

    def test_no_live_source_io_without_explicit_call(self):
        # Importing the module never opens Neo4j, starts containers,
        # reads environment variables or loads source task payloads.
        self.assertEqual(g.OWNER,"hermes-local")
        self.assertIn("HermesCensusGeneration",g.LOCK_AND_READ)

    def test_no_task_ids_in_result(self):
        r=g.generation_locked_candidate(lambda:Session([row(1)]))
        self.assertNotIn("synthetic_0001",str(r))
        self.assertNotIn("session_0001",str(r))

    def test_writer_and_reader_same_owner_pin(self):
        self.assertIn("{owner:$owner}",g.LOCK_AND_READ)
        self.assertIn("{owner:$owner}",g.LOCK_AND_ADVANCE_WRITER)
        self.assertIn("SET g.read_gate",g.LOCK_AND_READ)
        self.assertIn("SET g.generation",g.LOCK_AND_ADVANCE_WRITER)


if __name__=="__main__":
    unittest.main()
