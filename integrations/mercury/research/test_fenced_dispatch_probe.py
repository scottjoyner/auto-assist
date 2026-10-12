"""No-network tests: externally fenced mock dispatch, not provider access."""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest
import uuid

import fenced_dispatch_probe as m

class FencedProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="mercury-fence-unit-")
        self.addCleanup(self.temp.cleanup)
        self.db=str(Path(self.temp.name)/"proxy.sqlite")
        self.lock=m.initialize(self.db)
        self.addCleanup(lambda: self.lock.close())
        self.keys={"node-x1":secrets.token_hex(32),"node-xwing":secrets.token_hex(32)}
        policy={"botId":"reviewer","provider":"fixture","model":"mock-model",
                "upstreamGroup":m.TEST_GROUP}
        self.cfg={"test_keys":self.keys,
                  "test_policy":{"node-x1":policy,"node-xwing":policy}}

    def request(self,node="node-x1",fence=1,job="job-001",**overrides):
        r={"job":job,"botId":"reviewer","provider":"fixture",
           "model":"mock-model","upstreamGroup":m.TEST_GROUP,"fence":fence}
        r.update(overrides)
        data={"node":node,"nonce":str(uuid.uuid4()),"op":"mock_dispatch","request":r}
        return {"data":data,"mac":m.signature(self.keys[node],data)}

    def perform(self,request):
        db=m.database(self.db)
        try: return m.authorize(db,self.cfg,request)
        finally: db.close()

    def test_denial_until_local_operator_grants_fence(self):
        result=self.perform(self.request())
        self.assertFalse(result[1]["data"]["permitted"])
        m.local_promote(self.db,"node-x1",0)
        result=self.perform(self.request())
        self.assertTrue(result[1]["data"]["permitted"])
        self.assertTrue(m.check_signature(self.keys["node-x1"],result[1]["data"],result[1]["mac"]))

    def test_forged_copied_epoch_rejected_and_transfer(self):
        m.local_promote(self.db,"node-x1",0)
        code,answer=self.perform(self.request(node="node-xwing",fence=999))
        self.assertEqual(code,200)
        self.assertFalse(answer["data"]["permitted"])
        self.assertEqual(answer["data"]["reason"],"stale_or_wrong_issuer")
        self.assertTrue(self.perform(self.request(job="job-live-x1"))[1]["data"]["permitted"])
        m.local_promote(self.db,"node-xwing",1)
        self.assertFalse(self.perform(self.request(job="job-stale-x1"))[1]["data"]["permitted"])
        self.assertFalse(self.perform(self.request(node="node-xwing",fence=1))[1]["data"]["permitted"])
        self.assertTrue(self.perform(self.request(node="node-xwing",fence=2))[1]["data"]["permitted"])

    def test_stale_promotion_compare_and_swap_rejected(self):
        m.local_promote(self.db,"node-x1",0)
        with self.assertRaisesRegex(ValueError,"compare_and_swap"):
            m.local_promote(self.db,"node-xwing",0)
        self.assertEqual(m.get_fence(self.db),{"generation":1,"issuer":"node-x1"})

    def test_model_bot_upstream_identity_strict_allowlist(self):
        m.local_promote(self.db,"node-x1",0)
        for change in [{"botId":"untrusted"},{"provider":"paid-gateway"},
                       {"model":"other"},{"upstreamGroup":"forked-account"}]:
            result=self.request(**change)
            self.assertEqual(self.perform(result)[1]["data"]["reason"],"fixture_policy_denied")

    def test_signed_nonce_replay_and_tamper(self):
        m.local_promote(self.db,"node-x1",0)
        duplicate=self.request()
        self.assertEqual(self.perform(duplicate)[0],200)
        self.assertEqual(self.perform(duplicate)[0],409)
        forged=self.request()
        forged["data"]["request"]["fence"]=88
        self.assertEqual(self.perform(forged)[0],403)

    def test_wrong_physical_node_signer(self):
        m.local_promote(self.db,"node-x1",0)
        fake=self.request(node="node-xwing")
        fake["mac"]=m.signature(self.keys["node-x1"],fake["data"])
        self.assertEqual(self.perform(fake)[0],403)

    def test_reopen_keeps_external_fence(self):
        m.local_promote(self.db,"node-x1",0)
        self.lock.close()
        self.lock=m.initialize(self.db)
        self.assertEqual(m.get_fence(self.db),{"generation":1,"issuer":"node-x1"})
        self.assertTrue(self.perform(self.request())[1]["data"]["permitted"])

    def test_local_writer_exclusion(self):
        with self.assertRaises(BlockingIOError):
            m.initialize(self.db)

    def test_copying_proxy_database_creates_another_unsafe_authority(self):
        # A copied proxy, unlike a copied issuer DB, can authorize independently.
        # This intentionally proves that external state must be non-forkable.
        m.local_promote(self.db,"node-x1",0)
        source=m.database(self.db)
        clone_db=str(Path(self.temp.name)/"copied-proxy.sqlite")
        clone=m.database(clone_db)
        source.backup(clone)
        source.close()
        clone.close()
        msg1=self.request(job="fork-proxy-a")
        msg2=self.request(job="fork-proxy-b")
        real=self.perform(msg1)[1]["data"]["permitted"]
        shadow=m.database(clone_db)
        try:
            independent=m.authorize(shadow,self.cfg,msg2)[1]["data"]["permitted"]
        finally:
            shadow.close()
        self.assertTrue(real)
        self.assertTrue(independent)

    def test_hash_chain_tampering_detected(self):
        m.local_promote(self.db,"node-x1",0)
        self.perform(self.request())
        self.assertTrue(m.inspect(self.db)["verified"])
        c=m.database(self.db)
        c.execute("UPDATE audit SET value='{}' WHERE seq=1")
        c.close()
        with self.assertRaisesRegex(ValueError,"invalid_hash_link"):
            m.inspect(self.db)

    def test_zero_provider_transport_imported(self):
        # This code never creates a provider SDK or bearer authorization.
        source=Path(m.__file__).read_text()
        self.assertNotIn("from openai import",source)
        self.assertNotIn("Authorization: Bearer",source)

if __name__=="__main__":
    unittest.main(verbosity=2)
