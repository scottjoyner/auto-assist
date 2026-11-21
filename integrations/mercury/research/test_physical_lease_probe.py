"""Offline-only stdlib unittest for the disposable *non-production* transport."""
import contextlib
import io
import json
from pathlib import Path
import secrets
import shutil
import tempfile
import time
import unittest
import uuid

import physical_lease_probe as m


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mercury-lease-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = str(self.root / "authority.db")
        self.keys = {"node-x1": secrets.token_hex(32), "node-xwing": secrets.token_hex(32)}
        self.config = {
            "test_keys": self.keys,
            "test_policy": {
                n: {"botId": "reviewer", "provider": "fixture",
                    "model": "mock-model", "upstreamGroup": m.GROUP,
                    "maxInputTokens": 100, "maxOutputTokens": 10}
                for n in self.keys
            }
        }
        self.lock, self.epoch = m.init_authority(self.db, 2000)
        self.addCleanup(self.lock.close)

    def request(self, node="node-x1", bot="reviewer", task="task-00000001", **changes):
        payload = {"node":node,"nonce":str(uuid.uuid4()),"op":"acquire",
                   "request":{"task":task,"botId":bot,"provider":"fixture",
                              "model":"mock-model","upstreamGroup":m.GROUP,
                              "maxInputTokens":100,"maxOutputTokens":10},
                   "lease":None}
        payload["request"].update(changes)
        return {"payload":payload,"mac":m.mac(self.keys[node],payload)}

    def invoke(self, msg):
        con=m.connect(self.db)
        try: return m.handle(con,self.config,self.epoch,2000,msg)
        finally: con.close()

    def test_signed_identity_and_one_slot(self):
        first=self.request()
        code,body=self.invoke(first)
        self.assertEqual(code,200)
        self.assertEqual(body["payload"]["decision"],"admitted")
        self.assertTrue(m.valid_mac(self.keys["node-x1"],body["payload"],body["mac"]))
        code,other=self.invoke(self.request(node="node-xwing",task="task-00000002"))
        self.assertEqual(code,200)
        self.assertEqual(other["payload"]["decision"],"occupied_or_stale")
        self.assertIsNone(other["payload"]["result"])

    def test_wrong_bot_model_and_group_rejected(self):
        for changes in [{"botId":"intruder"},{"model":"paid-model"},
                        {"upstreamGroup":"fake-upstream"},{"maxInputTokens":101}]:
            msg=self.request()
            msg["payload"]["request"].update(changes)
            msg["mac"]=m.mac(self.keys["node-x1"],msg["payload"])
            code,answer=self.invoke(msg)
            self.assertEqual(code,200)
            self.assertEqual(answer["payload"]["decision"],"policy_denied")

    def test_tamper_and_replay_are_rejected(self):
        msg=self.request()
        msg["payload"]["request"]["model"]="unpaid-model"
        self.assertEqual(self.invoke(msg)[0],403)
        good=self.request()
        self.assertEqual(self.invoke(good)[0],200)
        self.assertEqual(self.invoke(good)[0],409)

    def test_distinct_key_rejected(self):
        msg=self.request()
        msg["mac"]=m.mac(secrets.token_hex(32),msg["payload"])
        self.assertEqual(self.invoke(msg)[0],403)

    def test_same_database_second_process_lock_is_denied(self):
        with self.assertRaises(BlockingIOError):
            m.init_authority(self.db,2000)

    def test_restart_epoch_holds_and_rejects_stale_lease(self):
        _,ans=self.invoke(self.request())
        lease=ans["payload"]["result"]
        self.lock.close()
        lock, epoch = m.init_authority(self.db,2000)
        self.addCleanup(lock.close)
        self.assertEqual(epoch,2)
        self.epoch=epoch
        code,competing=self.invoke(self.request(node="node-xwing",task="task-00000002"))
        self.assertEqual(code,200)
        self.assertEqual(competing["payload"]["decision"],"occupied_or_stale")
        request=self.request()
        request["payload"]["op"]="renew"
        request["payload"]["lease"]=lease
        request["mac"]=m.mac(self.keys["node-x1"],request["payload"])
        code,stale=self.invoke(request)
        self.assertEqual(code,200)
        self.assertIsNone(stale["payload"]["result"])

    def test_hash_audit_detects_tampering(self):
        self.invoke(self.request())
        args=type("A",(),{"db":self.db})()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            m.inspect(args)
        self.assertTrue(json.loads(out.getvalue())["audit_verified"])
        con=m.connect(self.db)
        con.execute("UPDATE audit SET event_json='{}' WHERE seq=1")
        con.close()
        with self.assertRaises(ValueError):
            m.inspect(args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
