"""Entirely synthetic v1 offline property/relationship review-denial tests.

No production graph, credentials, identifiers, or private review evidence used.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GATE = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "endpoint_identity_disposition_gate.py"
)
SPEC = importlib.util.spec_from_file_location("offline_endpoint_disposition_gate", GATE)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def serialized(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fixture():
    common = [f"synthetic_common_{i:02}" for i in range(7)]
    owner = {
        "type": "EXPOSES",
        "direction": "IN",
        "neighbor_labels": ["SwarmNode"],
        "relation_property_keys": [],
    }
    serves = {
        "type": "SERVES",
        "direction": "OUT",
        "neighbor_labels": ["Model"],
        "relation_property_keys": [],
    }
    nodes = [
        {
            "labels": ["ServiceEndpoint"],
            "property_keys": common + [f"synthetic_service_{i:02}" for i in range(2)],
            "edges": [owner],
        },
        {
            "labels": ["ServiceEndpoint", "ModelEndpoint"],
            "property_keys": common + [f"synthetic_model_{i:02}" for i in range(10)],
            "edges": [owner, serves],
        },
    ]
    shape = {
        "schema": "assistx.fixed-readonly-duplicate-structure.v1",
        "observed_utc": "2026-11-03T00:00:00+00:00",
        "result": {
            "status": "READ_ONLY_DUPLICATE_SHAPE_PASS",
            "node_count": 2,
            "nodes": nodes,
            "read_only": True,
            "record_values_returned": False,
        },
        "container_image_pinned": True,
        "live_migration_approved": False,
        "production_mutated": False,
        "production_no_go": True,
        "query_returns_identifiers": False,
        "query_returns_property_values": False,
    }
    overlaps = {
        "schema": "assistx.live-duplicate-read-only-owner-overlap.v1",
        "time_utc": "2026-11-03T00:00:00+00:00",
        "container_pinned": True,
        "graph_mutations": False,
        "identifiers_returned": False,
        "live_reconciliation_approved": False,
        "production_no_go": True,
        "property_values_returned": False,
        "result": {
            "status": "READ_ONLY_AGGREGATE_PASS",
            "read_only": True,
            "counts": {
                "owners_a": 1,
                "owners_b": 1,
                "shared_owners": 1,
                "serves_a": 0,
                "serves_b": 1,
            },
        },
    }
    records = []
    for index, node in enumerate(nodes, 1):
        records.append(
            {
                "record_alias": f"colliding_shape_{index}",
                "labels": node["labels"],
                "property_count": len(node["property_keys"]),
                "properties": [
                    {
                        "key_name": key,
                        "current_value": "NOT_READ",
                        "disposition": "UNREVIEWED_HOLD",
                        "owner_approval_digest": None,
                        "consumer_dependency_reviewed": False,
                    }
                    for key in node["property_keys"]
                ],
                "relationships": [
                    {
                        "type": edge["type"],
                        "direction": edge["direction"],
                        "neighbor_labels": edge["neighbor_labels"],
                        "relationship_property_keys": edge["relation_property_keys"],
                        "referent_identity": "NOT_READ",
                        "migration_disposition": "UNREVIEWED_HOLD",
                    }
                    for edge in node["edges"]
                ],
                "survivor_selection": "NOT_AUTHORIZED",
            }
        )
    packet = {
        "schema": "assistx.endpoint-identity-duplicate-owner-review.v1",
        "created_utc": "2026-11-03T00:00:00+00:00",
        "purpose": "strictly synthetic fixture",
        "notes": "NO REAL NODE OR PROPERTY VALUES",
        "records": records,
        "live_duplicate_count": 2,
        "shared_owner_nodes": 1,
        "source_shape_sha256": hashlib.sha256(serialized(shape)).hexdigest(),
        "source_overlap_sha256": hashlib.sha256(serialized(overlaps)).hexdigest(),
        "graph_admission": "DENY",
        "production_no_go": True,
    }
    for key in gate.FALSE_FLAGS:
        packet[key] = False
    return packet, shape, overlaps


def evaluate(p=None, s=None, o=None):
    if p is None:
        p, s, o = fixture()
    return gate.assess(p, s, o, serialized(s), serialized(o))


class OfflineGateAdversarialTests(unittest.TestCase):
    def deny(self, mutate, contains=None):
        p, s, o = fixture()
        mutate(p, s, o)
        with self.assertRaises(gate.EvidenceDrift) as cm:
            evaluate(p, s, o)
        if contains:
            self.assertIn(contains, str(cm.exception))

    def test_complete_packet_still_holds(self):
        result = evaluate()
        self.assertEqual(result["status"], "HOLD_REVIEW_REQUIRED")
        self.assertEqual(result["property_counts"], [9, 17])
        self.assertFalse(result["production_release_authorized"])
        self.assertEqual(result["write_actions_executed"], 0)
        self.assertFalse(result["source_authenticity_outside_admin_verified"])

    def test_missing_property_denied(self):
        self.deny(
            lambda p, s, o: p["records"][1]["properties"].pop(), "PROPERTY_COUNT_DRIFT"
        )

    def test_substituted_property_same_count_denied(self):
        self.deny(
            lambda p, s, o: p["records"][0]["properties"][0].update(
                key_name="replacement"
            ),
            "PRIVATE_REVIEW_NOT_EQUAL",
        )

    def test_duplicate_property_name_denied(self):
        def change(p, s, o):
            p["records"][0]["properties"][0]["key_name"] = p["records"][0][
                "properties"
            ][1]["key_name"]

        self.deny(change, "DUPLICATE_NAME")

    def test_extra_property_denied_even_if_count_updated(self):
        def change(p, s, o):
            r = p["records"][1]
            r["properties"].append(copy.deepcopy(r["properties"][0]))
            r["properties"][-1]["key_name"] = "synthetic_private_field"
            r["property_count"] += 1

        self.deny(change, "PRIVATE_REVIEW_NOT_EQUAL")

    def test_invented_promotion_denied(self):
        self.deny(
            lambda p, s, o: p.update(graph_admission="ALLOW"),
            "UNAUTHORIZED_RELEASE_CLAIM",
        )

    def test_false_no_go_denied(self):
        self.deny(
            lambda p, s, o: p.update(production_no_go=False),
            "UNAUTHORIZED_RELEASE_CLAIM",
        )

    def test_present_private_property_value_denied(self):
        self.deny(
            lambda p, s, o: p["records"][0]["properties"][0].update(
                current_value="synthetic_private_value"
            ),
            "PRIVATE_VALUE_INCLUDED",
        )

    def test_unverified_approver_denied(self):
        self.deny(
            lambda p, s, o: p["records"][0]["properties"][0].update(
                owner_approval_digest="a" * 64
            ),
            "PROPERTY_REVIEW_NOT_APPROVED",
        )

    def test_unverified_consumer_review_denied(self):
        self.deny(
            lambda p, s, o: p["records"][1]["properties"][0].update(
                consumer_dependency_reviewed=True
            ),
            "PROPERTY_REVIEW_NOT_APPROVED",
        )

    def test_survivor_selection_denied(self):
        self.deny(
            lambda p, s, o: p["records"][0].update(survivor_selection="model"),
            "SURVIVOR_SELECTED",
        )

    def test_unreviewed_edge_must_remain_hold(self):
        self.deny(
            lambda p, s, o: p["records"][1]["relationships"][1].update(
                migration_disposition="REWIRE"
            ),
            "RELATIONSHIP_UNAUTHORIZED",
        )

    def test_edge_direction_denied(self):
        self.deny(
            lambda p, s, o: p["records"][1]["relationships"][1].update(direction="IN"),
            "EDGE_SHAPE_DRIFT",
        )

    def test_extra_relationship_denied(self):
        self.deny(
            lambda p, s, o: p["records"][0]["relationships"].append(
                copy.deepcopy(p["records"][1]["relationships"][1])
            ),
            "EDGE_SHAPE_DRIFT",
        )

    def test_owner_overlap_change_denied(self):
        self.deny(
            lambda p, s, o: o["result"]["counts"].update(shared_owners=0),
            "EVIDENCE_HASH_MISMATCH",
        )

    def test_hash_replay_wrong_receipt_denied(self):
        self.deny(
            lambda p, s, o: p.update(source_shape_sha256="0" * 64),
            "EVIDENCE_HASH_MISMATCH",
        )

    def test_extra_unexpected_receipt_field_denied(self):
        self.deny(
            lambda p, s, o: s["result"].update(endpoint_value="do-not-publish"),
            "EVIDENCE_HASH_MISMATCH",
        )

    def test_unknown_neighbor_label_denied(self):
        def change(p, s, o):
            s["result"]["nodes"][0]["edges"][0]["neighbor_labels"] = ["UnknownNode"]
            p["source_shape_sha256"] = hashlib.sha256(serialized(s)).hexdigest()

        self.deny(change, "UNEXPECTED_NEIGHBOR_LABEL")

    def test_unknown_property_key_in_source_denied(self):
        def change(p, s, o):
            # Change a service-exclusive name so the seven shared names
            # remain unchanged and the precise cross-receipt test is reached.
            s["result"]["nodes"][0]["property_keys"][-1] = "synthetic_other"
            p["source_shape_sha256"] = hashlib.sha256(serialized(s)).hexdigest()

        self.deny(change, "PRIVATE_REVIEW_NOT_EQUAL")

    def test_same_admin_could_rewrite_both_but_never_get_authorization(self):
        p, s, o = fixture()
        new = "synthetic_simultaneous_rewrite"
        # Both records share this name. Same-admin synchronized tampering can
        # change both source and review while preserving ALL shape counts.
        for i in (0, 1):
            s["result"]["nodes"][i]["property_keys"][0] = new
            p["records"][i]["properties"][0]["key_name"] = new
        p["source_shape_sha256"] = hashlib.sha256(serialized(s)).hexdigest()
        result = evaluate(p, s, o)
        self.assertEqual(result["status"], "HOLD_REVIEW_REQUIRED")
        self.assertFalse(result["source_authenticity_outside_admin_verified"])
        self.assertFalse(result["production_release_authorized"])

    def test_source_with_new_17th_plus_key_denied(self):
        def change(p, s, o):
            s["result"]["nodes"][1]["property_keys"].append("synthetic_18th_key")
            p["source_shape_sha256"] = hashlib.sha256(serialized(s)).hexdigest()

        self.deny(change, "PINNED_PROPERTY_COUNT_DRIFT")

    def test_duplicate_json_key_denied_before_assessment(self):
        with self.assertRaises(gate.EvidenceDrift):
            gate.decode(b'{"ok":true,"ok":false}')

    def test_default_cli_no_evidence_no_access(self):
        run = subprocess.run(
            [sys.executable, str(GATE)],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(
            json.loads(run.stdout)["status"], "HOLD_PRIVATE_EVIDENCE_REQUIRED"
        )

    def test_cli_private_files_fidelity_and_no_names_in_output(self):
        p, s, o = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for name, item in (("packet", p), ("shape", s), ("overlap", o)):
                path = Path(tmp) / f"{name}.json"
                path.write_bytes(serialized(item))
                path.chmod(0o600)
                files.append(path)
            call = [
                sys.executable,
                str(GATE),
                "--private-review",
                str(files[0]),
                "--private-shape",
                str(files[1]),
                "--private-owners",
                str(files[2]),
            ]
            passed = subprocess.run(
                call, capture_output=True, text=True, timeout=8, check=False
            )
            self.assertEqual(passed.returncode, 2)
            self.assertEqual(
                json.loads(passed.stdout)["status"], "HOLD_REVIEW_REQUIRED"
            )
            self.assertNotIn("synthetic_common_00", passed.stdout)
            self.assertNotIn(str(files[0]), passed.stdout)
            files[0].chmod(0o644)
            denied = subprocess.run(
                call, capture_output=True, text=True, timeout=8, check=False
            )
            self.assertEqual(denied.returncode, 3)
            self.assertEqual(
                json.loads(denied.stdout)["reason"], "PRIVATE_FILE_PERMISSIONS"
            )

    def test_symlinked_input_denied(self):
        p, s, o = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / x for x in ("r.json", "s.json", "o.json")]
            for path, body in zip(paths, (p, s, o), strict=True):
                path.write_bytes(serialized(body))
                path.chmod(0o600)
            link = Path(tmp) / "link.json"
            link.symlink_to(paths[0])
            result = subprocess.run(
                [
                    sys.executable,
                    str(GATE),
                    "--private-review",
                    str(link),
                    "--private-shape",
                    str(paths[1]),
                    "--private-owners",
                    str(paths[2]),
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            self.assertEqual(result.returncode, 3)
            self.assertEqual(
                json.loads(result.stdout)["reason"], "PRIVATE_FILE_UNAVAILABLE"
            )


if __name__ == "__main__":
    unittest.main()
