"""Offline candidate compared with pinned legacy producer and real in-memory outbox."""
import copy
import pathlib
import sys
import unittest

import test_router_request_evidence_limitations as legacy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "research"))
from fleet_router_request_observation import (
    build_observation, observation_key, reconcile_built_observations,
    InvalidObservation, MAX_ROWS,
)


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        legacy.RouterEvidenceTests.setUpClass()

    def setUp(self):
        self.fixture = legacy.RouterEvidenceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.req = self.fixture.request
        self.req.metadata["assistx_executor"] = {
            "task_id":"task-A","claim_id":"claim-A","agent_id":"fleet-executor",
            "projection_generation":3,
        }

    def build(self, oid="observation-A", usage=None, stage="primary", status=200, error=None):
        return build_observation(observation_id=oid,request=self.req,provider="provider",
            model="m",stage=stage,status_code=status,usage=usage,
            estimate=self.fixture.estimate,error=error)

    def test_lineage_preserved_but_unverified(self):
        self.fixture.event(usage={"prompt_tokens":5,"completion_tokens":3,"total_tokens":8})
        old=self.fixture.outbox.recent()[0]["payload"]
        self.assertNotIn("claim_id",old)
        r=self.build()
        self.assertEqual(r["lineage"]["claimed"]["claim_id"],"claim-A")
        self.assertEqual(r["lineage"]["state"],"DECLARED_UNVERIFIED")
        self.assertFalse(r["lineage_authenticated"])
        self.assertFalse(r["physical_execution_verified"])
        self.assertIsNone(r["accepted_work_units"])

    def test_missing_and_zero_no_longer_conflated(self):
        a=self.build()
        b=self.build(oid="B",usage={"prompt_tokens":0,"completion_tokens":0,"total_tokens":0})
        self.assertEqual(a["usage"]["state"],"NOT_REPORTED")
        self.assertIsNone(a["usage"]["derived_total"])
        self.assertEqual(b["usage"]["state"],"COMPLETE_REPORTED")
        self.assertEqual(b["usage"]["derived_total"],0)
        self.assertEqual(a["estimates"],b["estimates"])
        self.assertEqual(a["estimates"]["input_tokens"],17)

    def test_strict_usage_types_and_consistency(self):
        for value in (True,-1,1.0,"1",1<<63):
            self.assertEqual(self.build(usage={"prompt_tokens":value})["usage"]["state"],"INVALID")
        for value in ({"prompt_tokens":3,"completion_tokens":4,"total_tokens":9},
                      {"prompt_tokens":(1<<63)-1,"completion_tokens":1},[]):
            self.assertEqual(self.build(usage=value)["usage"]["state"],"INVALID")
        self.assertEqual(self.build(usage={"completion_tokens":0})["usage"]["state"],"PARTIAL_REPORTED")

    def test_distinct_attempts_same_stage_survive_existing_outbox(self):
        a=self.build(usage={"prompt_tokens":5,"completion_tokens":3})
        b=self.build(oid="observation-B",usage={"prompt_tokens":6,"completion_tokens":9})
        outbox=self.fixture.outbox
        event=outbox.__class__.__module__
        import sys
        event_class=sys.modules[event].OutboxEvent
        for row in (a,b,a):
            outbox.enqueue(event_class(event_type="research.request_observation",
                idempotency_key=observation_key(row),payload=row))
        rows=outbox.recent()
        self.assertEqual(len(rows),2)
        self.assertEqual({r["payload"]["usage"]["derived_total"] for r in rows},{8,15})
        r=reconcile_built_observations([a,b,a])
        self.assertEqual(r["unique_observation_ids"],2)
        self.assertEqual(r["distinct_request_ids"],1)
        self.assertEqual(r["exact_replays"],1)
        self.assertIsNone(r["actual_endpoint_calls"])

    def test_same_id_conflict_holds_before_publication(self):
        a=self.build()
        b=self.build(usage={"prompt_tokens":5,"completion_tokens":3})
        r=reconcile_built_observations([a,b])
        self.assertEqual(r["decision"],"HOLD")
        self.assertIsNone(r["unique_observation_ids"])
        self.assertEqual(r["conflicting_rows"],1)

    def test_untrusted_metadata_cannot_choose_observation_id(self):
        self.req.metadata["observation_id"]="spoofed"
        self.req.metadata["assistx_executor"]["observation_id"]="spoofed"
        self.assertEqual(self.build()["observation_id"],"observation-A")

    def test_conflicting_and_invalid_lineage(self):
        self.req.metadata["claim_id"]="different"
        self.assertEqual(self.build()["lineage"]["state"],"CONFLICTING_DECLARATION")
        self.assertIsNone(self.build()["lineage"]["claimed"])
        self.req.metadata["assistx_executor"]["projection_generation"]=True
        self.assertEqual(self.build()["lineage"]["state"],"INVALID_DECLARATION")
        del self.req.metadata["assistx_executor"]
        self.assertEqual(self.build()["lineage"]["state"],"NOT_DECLARED")

    def test_outcome_not_delivery_status(self):
        self.assertEqual(self.build(status=None)["outcome"],"STATUS_UNKNOWN")
        self.assertEqual(self.build(status=302)["outcome"],"HTTP_NON_SUCCESS_REPORTED")
        self.assertEqual(self.build(status=200,error=RuntimeError("PRIVATE"))["outcome"],"ERROR_REPORTED")
        self.assertNotIn("PRIVATE",str(self.build(error=RuntimeError("PRIVATE"))))
        self.assertEqual(self.build()["outcome"],"HTTP_SUCCESS_REPORTED")

    def test_bounds_and_no_authority_widening(self):
        for oid in ("","x"*513):
            with self.assertRaises(InvalidObservation):self.build(oid=oid)
        with self.assertRaises(InvalidObservation):self.build(status=True)
        with self.assertRaises(InvalidObservation):reconcile_built_observations([None]*(MAX_ROWS+1))
        row=copy.deepcopy(self.build());row["lineage_authenticated"]=True
        with self.assertRaises(InvalidObservation):reconcile_built_observations([row])


if __name__=="__main__":
    unittest.main(verbosity=2)
