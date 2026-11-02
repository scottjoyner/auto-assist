"""No-authority adversarial tests for harness2 paired shadow evidence."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/experiments/harness2_paired_shadow_gate.py"
spec = importlib.util.spec_from_file_location("shadow_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
A = "a" * 64
B = "b" * 64
C = "c" * 64
SHA = "6868817518876de8fd170c6a8354daca273b6191"
SENSITIVE = "do_not_report_this_session_secret"

def fixture(n=20, improved=True):
    rows = []
    for i in range(n):
        for variant in ("baseline", "candidate"):
            rows.append({
                "task_id": "task_%d" % i,
                "input_sha256": A,
                "judge_sha256": C,
                "model_sha256": B,
                "variant": variant,
                "completed": True,
                "judge_score": 0.6 if variant == "baseline" else (0.85 if improved else 0.5),
                "prompt_tokens": 100,
                "completion_tokens": 100 if variant == "baseline" else 90,
                "duration_ms": 120 if variant == "baseline" else 115,
                "raw_prompt": SENSITIVE,
                "raw_trace": SENSITIVE,
            })
    return {
        "schema_version": 1,
        "configuration": {
            "upstream_commit": SHA, "dataset_sha256": A,
            "model_sha256": B, "judge_sha256": C,
            "mode": "sequential", "k": 2, "seed": 42,
            "origin": "synthetic", "candidate_components": ["system_prompt"],
        },
        "observations": rows,
    }

class TestHarnessGate(unittest.TestCase):
    def test_positive_synthetic_signal_never_authorizes(self):
        result = gate.evaluate(fixture())
        self.assertEqual(result["signal"], "potential_improvement_for_independent_review")
        self.assertFalse(result["adoption_authorized"])
        self.assertFalse(result["quality_independently_verified"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertEqual(result["production_gate"], "HOLD")
        self.assertNotIn(SENSITIVE, json.dumps(result))
        self.assertEqual(result["paired_tasks"], 20)

    def test_fewer_than_twenty_is_not_evidence(self):
        self.assertEqual(gate.evaluate(fixture(2))["signal"], "insufficient_or_unfavorable_evidence")

    def test_unfavorable_effect_not_promoted(self):
        self.assertEqual(gate.evaluate(fixture(improved=False))["signal"], "insufficient_or_unfavorable_evidence")

    def test_changed_guardrail_fails(self):
        for component in ("guardrails", "plugins", "scripts"):
            d = fixture()
            d["configuration"]["candidate_components"].append(component)
            with self.subTest(component=component), self.assertRaises(gate.Rejected):
                gate.evaluate(d)

    def test_empty_or_duplicate_edits_fail(self):
        for components in ([], ["skills", "skills"], [123], ["tool_guidance", None]):
            d = fixture()
            d["configuration"]["candidate_components"] = components
            with self.subTest(components=components), self.assertRaises(gate.Rejected):
                gate.evaluate(d)

    def test_model_identity_drift_fails(self):
        d = fixture()
        d["observations"][0]["model_sha256"] = A
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_judge_identity_drift_fails(self):
        d = fixture()
        d["observations"][-1]["judge_sha256"] = A
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_task_input_drift_fails(self):
        d = fixture()
        d["observations"][0]["input_sha256"] = B
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_missing_token_counts_are_not_zero(self):
        d = fixture()
        del d["observations"][0]["completion_tokens"]
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_no_unpaired_tasks(self):
        d = fixture()
        d["observations"][0]["task_id"] = "newtask"
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_duplicate_variant_fails(self):
        d = fixture()
        d["observations"][0]["variant"] = "candidate"
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_nonfinite_or_invalid_scores_fail(self):
        for value in (math.nan, math.inf, -0.1, 1.001, "0.5", True):
            d = fixture()
            d["observations"][0]["judge_score"] = value
            with self.subTest(value=value), self.assertRaises(gate.Rejected):
                gate.evaluate(d)

    def test_nonint_token_counts_fail(self):
        for value in (4.2, "4", -1, True):
            d = fixture()
            d["observations"][0]["prompt_tokens"] = value
            with self.subTest(value=value), self.assertRaises(gate.Rejected):
                gate.evaluate(d)

    def test_incomplete_nonzero_scoring_fails(self):
        d = fixture()
        d["observations"][0]["completed"] = False
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_unbounded_recursion_fails(self):
        d = fixture()
        d["configuration"]["k"] = 9
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_no_origin_fails(self):
        d = fixture()
        d["configuration"]["origin"] = "independently_verified"
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_bad_sha_fails(self):
        d = fixture()
        d["configuration"]["upstream_commit"] = "notpinned"
        with self.assertRaises(gate.Rejected):
            gate.evaluate(d)

    def test_completion_regression_not_promoted(self):
        d = fixture()
        d["observations"][1]["completed"] = False
        d["observations"][1]["judge_score"] = 0
        self.assertEqual(gate.evaluate(d)["signal"], "insufficient_or_unfavorable_evidence")

    def test_token_inflation_not_promoted(self):
        d = fixture()
        for row in d["observations"]:
            if row["variant"] == "candidate":
                row["completion_tokens"] = 1000
        self.assertEqual(gate.evaluate(d)["signal"], "insufficient_or_unfavorable_evidence")

    def test_cli_success_without_secret_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            p = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path)],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn(SENSITIVE, p.stdout + p.stderr)
            self.assertFalse(json.loads(p.stdout)["dispatch_allowed"])

    def test_cli_bad_json_fails_without_raw_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text('{"secret":"'+SENSITIVE+'", "invalid":NaN}')
            p = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path)],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 2)
            self.assertNotIn(SENSITIVE, p.stdout + p.stderr)

if __name__ == "__main__":
    unittest.main()
