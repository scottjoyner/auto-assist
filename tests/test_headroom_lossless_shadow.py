"""Pure-Python, zero-network tests of the Headroom synthetic shadow gate."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts/experiments/headroom_lossless_shadow.py"
spec = importlib.util.spec_from_file_location("headroom_lossless_shadow", SOURCE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def review(name="cargo_tests", output=None, **kwargs):
    raw = probe.synthetic_cases()[name]
    if output is None:
        output = raw
    return probe.assess(name,raw,output,True,0,20.0,1000,1000,**kwargs)


class TestHeadroomGate(unittest.TestCase):
    def test_four_deterministic_cases(self):
        one=probe.synthetic_cases()
        two=probe.synthetic_cases()
        self.assertEqual(one,two)
        self.assertEqual(set(one),{"cargo_tests","pretty_json","ndjson","application_log"})

    def test_all_sources_have_failure_and_location(self):
        for label,raw in probe.synthetic_cases().items():
            with self.subTest(label=label):
                self.assertIn(probe.MARKER,raw)
                self.assertIn(probe.LOCATION,raw)

    def test_exact_unchanged_receipt_is_safe_but_never_admitted(self):
        for name in probe.synthetic_cases():
            with self.subTest(name=name):
                item=review(name)
                self.assertTrue(item["synthetic_evidence_gate_passed"])
                self.assertEqual(item["source_bytes"],item["output_bytes"])
                self.assertEqual(item["byte_reduction_pct"],0)
                self.assertFalse(item["actual_model_tokens_verified"])
                self.assertEqual(item["production_gate"],"HOLD")
                self.assertFalse(item["deployment_allowed"])
                self.assertFalse(item["automated_agent_hooks_allowed"])
                self.assertFalse(item["claims_custody_authorized"])
                self.assertNotIn(probe.MARKER,json.dumps(item))

    def test_missing_marker_fails_gate(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        item=review(output=raw.replace(probe.MARKER,"OMITTED"))
        self.assertFalse(item["synthetic_evidence_gate_passed"])
        self.assertFalse(item["marker_preserved"])

    def test_missing_source_location_fails_gate(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        item=review(output=raw.replace(probe.LOCATION,"UNKNOWN"))
        self.assertFalse(item["synthetic_evidence_gate_passed"])

    def test_claimed_lossless_does_not_authorize_changed_output(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        item=review(output=raw.replace("test synthetic::case_0001 ... ok\n",""))
        self.assertTrue(item["marker_preserved"])
        self.assertTrue(item["source_location_preserved"])
        self.assertTrue(item["library_reported_lossless"])
        self.assertFalse(item["evidence_roundtrip_independently_verified"])
        self.assertFalse(item["synthetic_evidence_gate_passed"])

    def test_expanded_output_is_not_verified(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        item=review(output=raw+"additional content\n")
        self.assertFalse(item["synthetic_evidence_gate_passed"])

    def test_noncanonical_source_rejected(self):
        with self.assertRaises(probe.EvidenceRejected):
            probe.assess("cargo_tests","made-up", "made-up",True,0,1.0,10,10)

    def test_unknown_case_rejected(self):
        with self.assertRaises(probe.EvidenceRejected):
            probe.assess("unknown","x","x",True,0,1.0,10,10)

    def test_bad_lossless_type_rejected(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        with self.assertRaises(probe.EvidenceRejected):
            probe.assess("cargo_tests",raw,raw,1,0,1.0,10,10)

    def test_bad_reversion_rejected(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        for value in (-1,4,True,"1"):
            with self.subTest(value=value),self.assertRaises(probe.EvidenceRejected):
                probe.assess("cargo_tests",raw,raw,True,value,1.0,10,10)

    def test_bad_timing_rejected(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        for value in (-1,float("nan"),float("inf"),"1"):
            with self.subTest(value=value),self.assertRaises(probe.EvidenceRejected):
                probe.assess("cargo_tests",raw,raw,True,0,value,10,10)

    def test_bad_estimated_tokens_rejected(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        for value in (-1,True,"10"):
            with self.subTest(value=value),self.assertRaises(probe.EvidenceRejected):
                probe.assess("cargo_tests",raw,raw,True,0,1.0,value,10)

    def test_output_cap_enforced(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        with self.assertRaises(probe.EvidenceRejected):
            probe.assess("cargo_tests",raw,"x"*(probe.MAX_BYTES+1),True,0,1.0,10,10)

    def test_offline_env_requires_every_setting(self):
        sample=dict(probe.OFFLINE_ENV)
        probe.require_offline(sample)
        for k in sample:
            with self.subTest(name=k):
                clone=dict(sample)
                clone.pop(k)
                with self.assertRaises(probe.EvidenceRejected):
                    probe.require_offline(clone)

    def test_bad_telemetry_values_refused(self):
        x=dict(probe.OFFLINE_ENV)
        x["HEADROOM_TELEMETRY"]="on"
        with self.assertRaises(probe.EvidenceRejected):
            probe.require_offline(x)

    def test_cli_does_not_import_native_dependencies_by_default(self):
        p=subprocess.run([sys.executable,"-S",str(SOURCE)],capture_output=True,text=True,timeout=8)
        self.assertEqual(p.returncode,2)
        self.assertEqual(p.stdout,"")
        self.assertNotIn("Traceback",p.stderr)

    def test_cli_exec_refuses_without_offline_env(self):
        p=subprocess.run([sys.executable,"-S",str(SOURCE),"--execute-synthetic"],
                         capture_output=True,text=True,timeout=8,env={"PATH":"/usr/bin:/bin"})
        self.assertEqual(p.returncode,2)
        self.assertNotIn("Traceback",p.stderr)

    def test_deps_not_loaded_in_pure_validation(self):
        review()
        self.assertNotIn("headroom",sys.modules)

    def test_sha_changes_when_content_changes(self):
        raw=probe.synthetic_cases()["cargo_tests"]
        item=review(output=raw+"\n")
        self.assertNotEqual(item["source_sha256"],item["output_sha256"])


if __name__=="__main__":
    unittest.main()
