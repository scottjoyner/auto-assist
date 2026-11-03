"""Fail-closed offline contracts for the explicitly synthetic migration test."""

from __future__ import annotations

import copy
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "endpoint_identity_collision_rehearsal_disposable.py"
spec = importlib.util.spec_from_file_location("collision_lab_guard", SOURCE)
assert spec and spec.loader
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)

OWNER = "scratch-review-only:owner"
EID = "scratch-review-only:endpoint"


def fixture():
    return [
        dict(
            labels=["ServiceEndpoint"],
            owners=[OWNER],
            kind="model_endpoint",
            mid=None,
            port=4321,
            relationship_types=["EXPOSES"],
            serves=0,
            endpoint=EID,
        ),
        dict(
            labels=["ServiceEndpoint", "ModelEndpoint"],
            owners=[OWNER],
            kind="model_endpoint",
            mid=EID,
            port=None,
            relationship_types=["EXPOSES", "SERVES"],
            serves=1,
            endpoint=EID,
        ),
    ]


class CollisionLabGuardTests(unittest.TestCase):
    def assessment(self, value):
        return lab.assess_exact_synthetic_pair(value, expected_owner=OWNER, expected_endpoint=EID)

    def test_known_synthetic_pair_only_reviewable_not_authorized(self):
        r = self.assessment(fixture())
        self.assertEqual(r["status"], "SYNTHETIC_PAIR_STRUCTURALLY_REVIEWABLE_ONLY")
        self.assertTrue(r["requires_external_operator_approval"])
        self.assertFalse(r["production_release_authorized"])

    def test_refuses_missing_or_third_record(self):
        for x in ([], fixture()[:1], fixture() + [fixture()[0]]):
            with self.subTest(n=len(x)), self.assertRaises(lab.SyntheticCollisionHold):
                self.assessment(x)

    def test_refuses_foreign_ownership(self):
        x = fixture()
        x[1]["owners"] = ["other-node"]
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_refuses_unexpected_incident_edge(self):
        x = fixture()
        x[0]["relationship_types"].append("DEPENDS_ON")
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_refuses_unknown_extra_label(self):
        x = fixture()
        x[1]["labels"].append("PrivilegedMarker")
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_refuses_malformed_label_type(self):
        x = fixture()
        x[0]["labels"].append(["unexpected"])
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_refuses_malformed_edge_type(self):
        x = fixture()
        x[0]["relationship_types"].append(["unexpected"])
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_refuses_divergent_model_identity(self):
        x = fixture()
        x[1]["mid"] = "unrelated"
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_refuses_conflicting_service_type(self):
        x = fixture()
        x[0]["kind"] = "ordinary"
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_refuses_unexpected_properties_shape(self):
        x = fixture()
        x[0]["private_token"] = "synthetic"
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_refuses_mutated_model_relationship_count(self):
        x = fixture()
        x[1]["serves"] = 2
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_same_model_twice_refuses(self):
        x = fixture()
        x[0] = copy.deepcopy(x[1])
        with self.assertRaises(lab.SyntheticCollisionHold):
            self.assessment(x)

    def test_default_never_calls_docker(self):
        p = subprocess.run([sys.executable, str(SOURCE)], capture_output=True, text=True, timeout=8)
        self.assertEqual(p.returncode, 2)
        self.assertIn("HOLD_EXPLICIT_DISPOSABLE_OPT_IN_REQUIRED", p.stdout)
        self.assertIn('"docker_commands_executed": false', p.stdout)


if __name__ == "__main__":
    unittest.main()
