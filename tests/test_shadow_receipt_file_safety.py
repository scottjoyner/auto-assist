import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-system-one-shadow-receipt.py"
spec = importlib.util.spec_from_file_location("validator", SCRIPT)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
from assistx.system_one_receipt import sha256_json


class ReceiptFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.request = self.root / "request.json"
        self.response = self.root / "response.json"
        self.output = self.root / "result.json"
        self.request.write_text('{"state":{"utterance":"fixture"}}')
        questions = [{"name":"route","type":"choice","options":["a","b"],
                      "probabilities":[0.9,0.1],"choice":"a","confidence":0.9,
                      "noul":None,"score":None}]
        self.receipt = {"contract":"assistx-agent-policy-v1","decision_receipt":{
            "schema":"system-one-decision-receipt-v1","input_sha256":"1"*64,
            "question_schema_sha256":"2"*64,"candidate_set_sha256":"3"*64,
            "response_sha256":sha256_json(questions),
            "provider":{"provider_id":"fixture","provider_version":"1","model_id":"m"},
            "latency_ms":1.0,"questions":questions}}
        self.response.write_text(json.dumps(self.receipt))

    def run_cli(self, output=None):
        args = [sys.executable,"-B",str(SCRIPT),"--request",str(self.request),
                "--response",str(self.response)]
        if output is not None:
            args += ["--output",str(output)]
        return subprocess.run(args,capture_output=True,text=True,timeout=5)

    def assert_blocked(self, result):
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn("BLOCKED",result.stderr)
        self.assertNotIn("PASS",result.stderr)
        self.assertNotIn("PRIVATE_SENTINEL",result.stderr)
        self.assertEqual(list(self.root.glob(".shadow-receipt-*")),[])

    def test_create_only_private_report(self):
        r=self.run_cli(self.output)
        self.assertEqual(r.returncode,0,r.stderr)
        data=json.loads(self.output.read_text())
        self.assertEqual(data["status"],"pass")
        self.assertFalse(data["authority"]["dispatch_allowed"])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode),0o600)
        before=self.output.read_bytes()
        self.assert_blocked(self.run_cli(self.output))
        self.assertEqual(self.output.read_bytes(),before)

    def test_stdout_compatible(self):
        r=self.run_cli()
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"],"pass")

    def test_output_aliases_preserved(self):
        before=self.request.read_bytes()
        self.assert_blocked(self.run_cli(self.request))
        self.assertEqual(self.request.read_bytes(),before)
        self.output.symlink_to(self.request)
        self.assert_blocked(self.run_cli(self.output))
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(self.request.read_bytes(),before)

    def test_oversized_capture(self):
        self.request.write_bytes(b" "*(validator.MAX_CAPTURE_BYTES+1))
        self.assert_blocked(self.run_cli(self.output))
        self.assertFalse(self.output.exists())

    def test_symlink_and_fifo_rejected(self):
        original=self.root/"original.json"
        self.request.rename(original)
        self.request.symlink_to(original)
        self.assert_blocked(self.run_cli(self.output))
        self.request.unlink()
        os.mkfifo(self.request)
        self.assert_blocked(self.run_cli(self.output))

    def test_ambiguous_or_nonfinite_json(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":{"b":1,"b":2}}',b'{"a":NaN}',
                    b'{"a":Infinity}',b'{"a":-Infinity}',b'{"a":"\xff"}',b'[]'):
            with self.subTest(raw=raw):
                self.request.write_bytes(raw)
                self.assert_blocked(self.run_cli(self.output))
                self.assertFalse(self.output.exists())

    def test_depth_boundary_and_quoted_delimiters(self):
        for depth in (63,64):
            raw = b'{"a":'+b'['*(depth-1)+b'0'+b']'*(depth-1)+b'}'
            self.request.write_bytes(raw)
            self.assertIsInstance(validator._load_mapping(self.request,"request"),dict)
        raw = b'{"a":'+b'['*64+b'0'+b']'*64+b'}'
        self.request.write_bytes(raw)
        self.assert_blocked(self.run_cli(self.output))
        self.request.write_text(json.dumps({"a":'['*1000+'"{'+chr(92)+'}'*1000}))
        self.assertEqual(self.run_cli().returncode,0)

    def test_nested_limit_and_sanitized_validation(self):
        self.request.write_text('{"a":'+'['*2000+'0'+']'*2000+'}')
        self.assert_blocked(self.run_cli(self.output))
        self.request.write_text('{}')
        self.receipt["decision_receipt"]["provider"]["model_id"]={"PRIVATE_SENTINEL":1}
        self.response.write_text(json.dumps(self.receipt))
        self.assert_blocked(self.run_cli(self.output))

    def test_missing_parent_no_creation(self):
        p=self.root/"absent"/"report.json"
        self.assert_blocked(self.run_cli(p))
        self.assertFalse(p.parent.exists())

    def test_failed_fsync_never_publishes(self):
        with patch.object(validator.os,"fsync",side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                validator._publish_new_report(self.output,"{}")
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".shadow-receipt-*")),[])

    def test_concurrent_output_race_preserves_winner(self):
        args=[sys.executable,"-B",str(SCRIPT),"--request",str(self.request),
              "--response",str(self.response),"--output",str(self.output)]
        p=[subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        for proc in p:
            proc.communicate(timeout=5)
        self.assertEqual(sorted(proc.returncode for proc in p),[0,1])
        self.assertEqual(json.loads(self.output.read_text())["status"],"pass")
        self.assertEqual(list(self.root.glob(".shadow-receipt-*")),[])

if __name__=="__main__":
    unittest.main(verbosity=2)
