"""Synthetic, offline contract tests for the llmfit shadow-only adapter."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ADAPTER = Path(__file__).resolve().parents[1] / "scripts" / "experiments" / "llmfit_shadow_advisory.py"
SPEC = importlib.util.spec_from_file_location("llmfit_shadow_advisory", ADAPTER)
advisor = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(advisor)


def fixture():
    # Matches the fields served by llmfit-tui/src/serve_shared.rs:fit_to_json.
    return {
        "node": {"name": "xwing", "os": "linux"},
        "system": {"total_ram_gb": 32, "gpus": []},
        "returned_models": 2,
        "models": [
            {
                "name": "research/SmallModel", "best_quant": "Q4_K_M",
                "runtime": "llamacpp", "fit_level": "good",
                "score": 76.0, "disk_size_gb": 2.5, "estimated_tps": 25.0,
                "measured_tps": None, "installed": True,
            },
            {
                "name": "research/LargeModel", "best_quant": "Q4_K_M",
                "runtime": "llamacpp", "fit_level": "too_tight",
                "score": 18.0, "disk_size_gb": 50.0,
                "estimated_tps": 0.1, "measured_tps": 2.0, "installed": False,
            },
        ],
    }


def evaluate(data=None, **kwargs):
    return advisor.make_advisory(
        fixture() if data is None else data,
        expected_node="xwing",
        upstream_commit="a" * 40,
        **kwargs,
    )


class ShadowAdvisorTests(unittest.TestCase):
    def test_advice_never_admits_even_when_installed_or_measured(self):
        report = evaluate()
        self.assertFalse(report["dispatch_allowed"])
        self.assertFalse(report["physical_load_verified"])
        self.assertFalse(report["approval_or_reservation_verified"])
        self.assertFalse(report["authoritative_capacity"])
        self.assertTrue(report["models"][0]["llmfit_installed_flag"])
        for row in report["models"]:
            self.assertFalse(row["admitted"])
            self.assertFalse(row["dispatch_allowed"])
            self.assertEqual(row["physical_load_state"], "UNVERIFIED")
            self.assertIsNone(row["signed_runtime_id"])

    def test_rejects_wrong_host(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            advisor.make_advisory(fixture(), expected_node="joyner", upstream_commit="a" * 40)

    def test_rejects_node_without_identity(self):
        data = fixture()
        data["node"] = {}
        with self.assertRaises(ValueError):
            evaluate(data)

    def test_rejects_unrecognized_fit_level(self):
        data = fixture()
        data["models"][0]["fit_level"] = "authorized"
        with self.assertRaisesRegex(ValueError, "unknown fit level"):
            evaluate(data)

    def test_rejects_duplicate_model_and_quant(self):
        data = fixture()
        data["models"][1] = copy.deepcopy(data["models"][0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluate(data)

    def test_rejects_nonfinite_values(self):
        for x in (float("nan"), float("inf"), -1.0):
            with self.subTest(x=x):
                data = fixture()
                data["models"][0]["score"] = x
                with self.assertRaises(ValueError):
                    evaluate(data)

    def test_rejects_nonnumeric_score(self):
        data = fixture()
        data["models"][0]["score"] = "99"
        with self.assertRaises(ValueError):
            evaluate(data)

    def test_rejects_missing_installed_flag(self):
        data = fixture()
        del data["models"][0]["installed"]
        with self.assertRaises(ValueError):
            evaluate(data)

    def test_rejects_false_count(self):
        data = fixture()
        data["returned_models"] = 3
        with self.assertRaisesRegex(ValueError, "returned_models"):
            evaluate(data)

    def test_rejects_oversized_model_array(self):
        data = fixture()
        data["models"] = [{}] * 501
        with self.assertRaisesRegex(ValueError, "models"):
            evaluate(data)

    def test_disk_guard_is_only_estimate(self):
        report = evaluate(free_disk_gb=14.0, disk_reserve_gb=10)
        self.assertEqual(report["models"][0]["disk_guard"], "estimated_weight_and_reserve_fit")
        self.assertEqual(report["models"][1]["disk_guard"], "insufficient_for_estimated_weights_and_reserve")
        self.assertEqual(report["observed_free_disk_gb_unattested"], 14.0)
        self.assertFalse(report["dispatch_allowed"])

    def test_disk_guard_unverified_by_default(self):
        self.assertTrue(all(row["disk_guard"] == "not_checked" for row in evaluate()["models"]))

    def test_cli_offline_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            sample = Path(d) / "snapshot.json"
            sample.write_text(json.dumps(fixture()), encoding="utf-8")
            output = subprocess.run(
                [sys.executable, str(ADAPTER), "--snapshot", str(sample), "--node", "xwing",
                 "--upstream-commit", "a" * 40],
                capture_output=True, text=True, check=False, timeout=10,
            )
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertFalse(json.loads(output.stdout)["dispatch_allowed"])

    def test_cli_rejects_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            sample = Path(d) / "snapshot.json"
            sample.write_text('{"models": [NaN]}', encoding="utf-8")
            output = subprocess.run(
                [sys.executable, str(ADAPTER), "--snapshot", str(sample), "--node", "xwing",
                 "--upstream-commit", "a" * 40],
                capture_output=True, text=True, check=False, timeout=10,
            )
            self.assertEqual(output.returncode, 2)
            self.assertIn("rejected input", output.stderr)


if __name__ == "__main__":
    unittest.main()
