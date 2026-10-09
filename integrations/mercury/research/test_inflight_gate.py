"""Research-only verification: quarantined uncertainty must block new mock work."""
import json
from pathlib import Path
import secrets
import tempfile
import time
import unittest
import uuid

import inflight_gate as gate


class InflightGateTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix="mercury-inflight-unit-")
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.db=str(self.root/"inflight.sqlite")
        self.keys={n:secrets.token_hex(32) for n in ("node-x1","node-xwing")}
        self.witness_key=secrets.token_hex(32)
        p={"botId":"reviewer","provider":"fixture","model":"mock-model",
           "upstreamGroup":gate.GROUP}
        self.config={"test_keys":self.keys,"test_policy":{n:dict(p) for n in self.keys}}
        self.lock=gate.initialize(self.db)
        self.addCleanup(lambda:self.lock.close())

    def request(self, node="node-x1", op="begin", fence=1, job="job-0001",
                run_id=None, **updates):
        req={"job":job,"botId":"reviewer","provider":"fixture",
             "model":"mock-model","upstreamGroup":gate.GROUP,
             "fence":fence,"runId":run_id}
        req.update(updates)
        data={"node":node,"op":op,"nonce":str(uuid.uuid4()),"request":req}
        return {"data":data,"mac":gate.sign(self.keys[node],data)}

    def ask(self, msg):
        con=gate.connect(self.db)
        try:return gate.transact(con,self.config,msg)
        finally:con.close()

    def begin(self, node="node-x1",fence=1,job="job-0001"):
        code,body=self.ask(self.request(node=node,fence=fence,job=job))
        self.assertEqual(code,200)
        return body["data"]

    def test_no_issuer_denies(self):
        self.assertEqual(self.begin()["reason"],"stale_issuer_or_fence")

    def test_active_single_capacity(self):
        gate.promote(self.db,"node-x1",0)
        first=self.begin()
        self.assertEqual(first["reason"],"mock_started")
        self.assertEqual(self.begin(job="job-0002")["reason"],"capacity_held_or_uncertain")
        self.assertEqual(gate.inspect(self.db)["slot"]["state"],"active")

    def test_current_owner_can_finish_and_release(self):
        gate.promote(self.db,"node-x1",0)
        run=self.begin()["result"]["runId"]
        code,finish=self.ask(self.request(op="finish",run_id=run))
        self.assertEqual(code,200)
        self.assertTrue(finish["data"]["result"])
        self.assertIsNone(gate.inspect(self.db)["slot"])
        self.assertEqual(self.begin(job="job-0002")["reason"],"mock_started")

    def test_promote_marks_active_uncertain_and_holds_slot(self):
        gate.promote(self.db,"node-x1",0)
        first=self.begin()["result"]
        promote=gate.promote(self.db,"node-xwing",1)
        self.assertEqual(promote["quarantinedActive"],1)
        slot=gate.inspect(self.db)["slot"]
        self.assertEqual(slot["state"],"quarantined")
        self.assertEqual(self.begin(node="node-xwing",fence=2,job="job-0002")["reason"],
                         "capacity_held_or_uncertain")
        old=self.ask(self.request(op="finish",run_id=first["runId"]))[1]["data"]
        self.assertFalse(old["result"])
        ack=self.ask(self.request(op="cancel_ack",run_id=first["runId"]))[1]["data"]
        self.assertEqual(ack["reason"],"worker_ack_not_independent_proof")
        self.assertEqual(gate.inspect(self.db)["slot"]["state"],"quarantined")

    def test_valid_fixture_witness_allows_explicit_reconcile(self):
        gate.promote(self.db,"node-x1",0)
        first=self.begin()["result"]
        gate.promote(self.db,"node-xwing",1)
        receipt=gate.make_witness(self.witness_key,first["runId"],2)
        path=self.root/"receipt.json"
        path.write_text(json.dumps(receipt))
        key_path=self.root/"witness.key"
        key_path.write_text(self.witness_key)
        self.assertTrue(gate.reconcile(self.db,path,key_path)["reconciled"])
        self.assertIsNone(gate.inspect(self.db)["slot"])
        self.assertEqual(self.begin(node="node-xwing",fence=2,job="job-0002")["reason"],
                         "mock_started")

    def test_old_generation_or_wrong_run_witness_never_reconciles(self):
        gate.promote(self.db,"node-x1",0)
        first=self.begin()["result"]
        gate.promote(self.db,"node-xwing",1)
        key=self.root/"w.key";key.write_text(self.witness_key)
        for r in [
            gate.make_witness(self.witness_key,first["runId"],1),
            gate.make_witness(self.witness_key,"other-run-id",2),
            gate.make_witness(secrets.token_hex(32),first["runId"],2),
        ]:
            path=self.root/"r.json";path.write_text(json.dumps(r))
            with self.assertRaises(ValueError):gate.reconcile(self.db,path,key)
        self.assertEqual(gate.inspect(self.db)["slot"]["state"],"quarantined")

    def test_boot_after_crash_quarantines_inflight_without_promotion(self):
        gate.promote(self.db,"node-x1",0)
        run=self.begin()["result"]["runId"]
        self.lock.close()
        self.lock=gate.initialize(self.db)
        slot=gate.inspect(self.db)["slot"]
        self.assertEqual(slot["runId"],run)
        self.assertEqual(slot["state"],"quarantined")
        self.assertEqual(self.begin(job="job-0002")["reason"],"capacity_held_or_uncertain")

    def test_hmac_tamper_and_nonce_replay(self):
        gate.promote(self.db,"node-x1",0)
        msg=self.request()
        self.assertEqual(self.ask(msg)[0],200)
        self.assertEqual(self.ask(msg)[0],409)
        changed=self.request()
        changed["data"]["request"]["model"]="paid-model"
        self.assertEqual(self.ask(changed)[0],403)

    def test_unknown_bot_model_and_upstream_denied(self):
        gate.promote(self.db,"node-x1",0)
        for update in [{"botId":"other"},{"model":"paid-model"},
                       {"upstreamGroup":"other-group"},{"provider":"unknown"}]:
            msg=self.request(**update)
            code,reply=self.ask(msg)
            self.assertEqual(code,200)
            self.assertEqual(reply["data"]["reason"],"policy_denied")

    def test_stale_operator_compare_and_swap_denied(self):
        gate.promote(self.db,"node-x1",0)
        with self.assertRaises(ValueError):gate.promote(self.db,"node-xwing",0)

    def test_audit_chain_detects_local_mutation(self):
        gate.promote(self.db,"node-x1",0)
        self.begin()
        self.assertTrue(gate.inspect(self.db)["verified"])
        con=gate.connect(self.db)
        con.execute("UPDATE audit SET event='{}' WHERE seq=1")
        con.close()
        with self.assertRaises(ValueError):gate.inspect(self.db)

    def test_copying_gate_database_still_forks_authority(self):
        # Explicit negative: copied downstream gate state defeats local control.
        gate.promote(self.db,"node-x1",0)
        src=gate.connect(self.db)
        dstdb=str(self.root/"clone.sqlite")
        dst=gate.connect(dstdb)
        src.backup(dst)
        src.close();dst.close()
        a=self.begin(job="job-fork-a")["result"]
        con=gate.connect(dstdb)
        try:
            b=gate.transact(con,self.config,self.request(job="job-fork-b"))[1]["data"]["result"]
        finally:con.close()
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)

    def test_witness_replay_does_not_clear_new_work(self):
        gate.promote(self.db,"node-x1",0)
        first=self.begin()["result"]
        gate.promote(self.db,"node-xwing",1)
        key=self.root/"w.key";key.write_text(self.witness_key)
        path=self.root/"receipt.json"
        path.write_text(json.dumps(gate.make_witness(self.witness_key,first["runId"],2)))
        gate.reconcile(self.db,path,key)
        second=self.begin(node="node-xwing",fence=2,job="job-0002")["result"]
        with self.assertRaises(ValueError):gate.reconcile(self.db,path,key)
        self.assertEqual(gate.inspect(self.db)["slot"]["runId"],second["runId"])


if __name__=="__main__":
    unittest.main(verbosity=2)
