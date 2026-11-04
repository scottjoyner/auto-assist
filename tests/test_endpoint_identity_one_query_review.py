"""Synthetic-only linkage/recency negative controls for a non-authorizing gate."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "endpoint_identity_one_query_review.py"


def load_known_file(path: Path, name: str):
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Expected synthetic research file unavailable")
    instance = module_from_spec(spec)
    spec.loader.exec_module(instance)
    return instance


one = load_known_file(CLI, "_pinned_one_query_review")
gate = one.base
fixtures = load_known_file(
    ROOT / "tests" / "test_endpoint_identity_disposition_gate.py",
    "_pinned_synthetic_disposition_fixture",
)
fixture = fixtures.fixture
serialized = fixtures.serialized
FIXED_NOW = datetime(2026, 11, 4, 0, 30, tzinfo=timezone.utc)


def data():
    packet, shape, overlap = fixture()
    subj = {
        "schema": "assistx.single-transaction-local-coherence-receipt.v1",
        "observed_utc": (FIXED_NOW - timedelta(minutes=1)).isoformat(),
        "method": one.METHOD,
        "container_id_sha256": "a" * 64,
        "image_id_sha256": "b" * 64,
        "independent_operator_verified": False,
        "operator_controlled_read_only_evidence": True,
        "subject_values_or_ids_returned": False,
        "production_mutated": False,
        "production_no_go": True,
        "result": {
            "schema": "assistx.one-query-endpoint-collision-census.v1",
            "status": "READ_ONLY_SINGLE_STATEMENT_PASS",
            "read_only_requested": True,
            "values_returned": False,
            "identifiers_returned": False,
            "nodes": copy.deepcopy(shape["result"]["nodes"]),
            "counts": copy.deepcopy(overlap["result"]["counts"]),
        },
    }
    return packet, shape, overlap, subj


def evaluate(
    packet=None, shape=None, overlap=None, subj=None, *, digest=None, now=FIXED_NOW
):
    if packet is None:
        packet, shape, overlap, subj = data()
    blob = serialized(subj)
    return one.assess_with_one_query(
        packet,
        shape,
        overlap,
        subj,
        shape_bytes=serialized(shape),
        overlap_bytes=serialized(overlap),
        single_bytes=blob,
        expected_single_sha256=digest or hashlib.sha256(blob).hexdigest(),
        now=now,
    )


class OneQueryVerifierTests(unittest.TestCase):
    def deny(self, mutate, expected=None):
        p, s, o, u = data()
        mutate(p, s, o, u)
        with self.assertRaises(gate.EvidenceDrift) as error:
            evaluate(p, s, o, u)
        if expected:
            self.assertEqual(str(error.exception), expected)

    def test_complete_four_input_review_still_hold(self):
        out = evaluate()
        self.assertEqual(out["status"], "HOLD_ONE_QUERY_CONSISTENT_BUT_UNAPPROVED")
        self.assertFalse(out["serializable_snapshot_isolation_verified"])
        self.assertFalse(out["outside_operator_attestation_verified"])
        self.assertFalse(out["independent_custody_verified"])
        self.assertFalse(out["production_release_authorized"])
        self.assertEqual(out["write_actions_executed"], 0)

    def test_one_query_record_order_is_not_identity(self):
        p, s, o, u = data()
        u["result"]["nodes"].reverse()
        out = evaluate(p, s, o, u)
        self.assertEqual(out["status"], "HOLD_ONE_QUERY_CONSISTENT_BUT_UNAPPROVED")

    def test_stale_receipt_denied_even_if_rehashed(self):
        self.deny(
            lambda p, s, o, u: u.update(
                observed_utc=(FIXED_NOW - timedelta(hours=2)).isoformat()
            ),
            "ONE_QUERY_RECEIPT_TOO_OLD_OR_FUTURE",
        )

    def test_future_dated_receipt_denied(self):
        self.deny(
            lambda p, s, o, u: u.update(
                observed_utc=(FIXED_NOW + timedelta(minutes=3)).isoformat()
            ),
            "ONE_QUERY_RECEIPT_TOO_OLD_OR_FUTURE",
        )

    def test_unzoned_receipt_denied(self):
        self.deny(
            lambda p, s, o, u: u.update(observed_utc="2026-11-04T00:29:00"),
            "ONE_QUERY_TIME_UNZONED",
        )

    def test_invalid_date_denied(self):
        self.deny(
            lambda p, s, o, u: u.update(observed_utc="2026-14-99T00:00:00Z"),
            "ONE_QUERY_TIME_MALFORMED",
        )

    def test_unexpected_property_drift_denied(self):
        def change(p, s, o, u):
            u["result"]["nodes"][0]["property_keys"][-1] = "foreign_key"

        self.deny(change, "ONE_QUERY_SUBJECT_OR_PROPERTY_MISMATCH")

    def test_unexpected_relationship_denied(self):
        def change(p, s, o, u):
            u["result"]["nodes"][1]["edges"][1]["type"] = "UNREVIEWED_EDGE"

        self.deny(change, "UNKNOWN_RELATIONSHIP")

    def test_drifted_model_edge_neighbor_denied(self):
        def change(p, s, o, u):
            u["result"]["nodes"][1]["edges"][1]["neighbor_labels"] = ["Unexpected"]

        self.deny(change, "UNEXPECTED_NEIGHBOR_LABEL")

    def test_wrong_owner_overlap_denied(self):
        self.deny(
            lambda p, s, o, u: u["result"]["counts"].update(shared_owners=0),
            "ONE_QUERY_OWNER_COUNTER_MISMATCH",
        )

    def test_wrong_model_count_denied(self):
        self.deny(
            lambda p, s, o, u: u["result"]["counts"].update(serves_b=0),
            "ONE_QUERY_OWNER_COUNTER_MISMATCH",
        )

    def test_unsupported_query_method_denied(self):
        self.deny(
            lambda p, s, o, u: u.update(method="two separate reads"),
            "ONE_QUERY_PROVENANCE_NOT_DENYING_RELEASE",
        )

    def test_fake_independent_attestation_denied(self):
        self.deny(
            lambda p, s, o, u: u.update(independent_operator_verified=True),
            "ONE_QUERY_PROVENANCE_NOT_DENYING_RELEASE",
        )

    def test_fake_production_authorization_denied(self):
        self.deny(
            lambda p, s, o, u: u.update(production_no_go=False),
            "ONE_QUERY_PROVENANCE_NOT_DENYING_RELEASE",
        )

    def test_extra_schema_field_denied(self):
        self.deny(
            lambda p, s, o, u: u["result"].update(serializable_snapshot_verified=True),
            "ONE_QUERY_RESULT_NOT_READONLY",
        )

    def test_missing_one_query_node_denied(self):
        self.deny(
            lambda p, s, o, u: u["result"]["nodes"].pop(),
            "ONE_QUERY_DUPLICATE_COUNT_DRIFT",
        )

    def test_wrong_expected_digest_denied(self):
        p, s, o, u = data()
        with self.assertRaisesRegex(
            gate.EvidenceDrift, "ONE_QUERY_EXPECTED_DIGEST_MISMATCH"
        ):
            evaluate(p, s, o, u, digest="0" * 64)

    def test_same_admin_forges_all_documents_still_hold(self):
        p, s, o, u = data()
        # Coordinated same-admin substitutions can retain counts and digests;
        # the validator MUST never convert that into release authority.
        for i in (0, 1):
            s["result"]["nodes"][i]["property_keys"][0] = "SYNTHETIC_FORGED_SHARED"
            p["records"][i]["properties"][0]["key_name"] = "SYNTHETIC_FORGED_SHARED"
            u["result"]["nodes"][i]["property_keys"][0] = "SYNTHETIC_FORGED_SHARED"
        p["source_shape_sha256"] = hashlib.sha256(serialized(s)).hexdigest()
        outcome = evaluate(p, s, o, u)
        self.assertEqual(outcome["status"], "HOLD_ONE_QUERY_CONSISTENT_BUT_UNAPPROVED")
        self.assertFalse(outcome["outside_operator_attestation_verified"])
        self.assertFalse(outcome["production_release_authorized"])

    def test_imports_ignore_hostile_ambient_scripts_namespace(self):

        code = """
import importlib.util
import pathlib
import sys
import types
sys.modules['scripts'] = types.ModuleType('scripts')
path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('one_query_isolated_test', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.base.__file__ == str(path.with_name('endpoint_identity_disposition_gate.py'))
print('PINNED_SIBLING_IMPORT_PASS')
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(CLI)],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PINNED_SIBLING_IMPORT_PASS", result.stdout)

    def test_default_cli_denies_without_opening_files(self):
        p = subprocess.run(
            [sys.executable, str(CLI)],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        self.assertEqual(p.returncode, 2)
        self.assertEqual(
            json.loads(p.stdout)["status"], "HOLD_FOUR_PRIVATE_EVIDENCE_INPUTS_REQUIRED"
        )

    def test_cli_four_synthetic_private_inputs_always_hold(self):
        p, s, o, u = data()
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, obj in [
                ("review", p),
                ("shape", s),
                ("owners", o),
                ("single", u),
            ]:
                path = Path(tmp) / (name + ".json")
                path.write_bytes(serialized(obj))
                path.chmod(0o600)
                paths.append(path)
            args = [
                sys.executable,
                str(CLI),
                "--private-review",
                str(paths[0]),
                "--private-shape",
                str(paths[1]),
                "--private-owners",
                str(paths[2]),
                "--private-one-query",
                str(paths[3]),
                "--expected-one-query-sha256",
                hashlib.sha256(paths[3].read_bytes()).hexdigest(),
            ]
            # Real clock is now; use a fresh synthetic timestamp for CLI.
            u["observed_utc"] = datetime.now(timezone.utc).isoformat()
            paths[3].write_bytes(serialized(u))
            digest = hashlib.sha256(paths[3].read_bytes()).hexdigest()
            args[-1] = digest
            got = subprocess.run(
                args, capture_output=True, text=True, timeout=8, check=False
            )
            self.assertEqual(got.returncode, 2, got.stderr)
            verdict = json.loads(got.stdout)
            self.assertEqual(
                verdict["status"], "HOLD_ONE_QUERY_CONSISTENT_BUT_UNAPPROVED"
            )
            self.assertNotIn("synthetic_common_00", got.stdout)
            self.assertNotIn(str(paths[0]), got.stdout)
            args[-1] = "0" * 64
            denied = subprocess.run(
                args, capture_output=True, text=True, timeout=8, check=False
            )
            self.assertEqual(denied.returncode, 3)
            self.assertEqual(
                json.loads(denied.stdout)["reason"],
                "ONE_QUERY_EXPECTED_DIGEST_MISMATCH",
            )


if __name__ == "__main__":
    unittest.main()
