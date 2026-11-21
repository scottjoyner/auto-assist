"""Pure deny-only tests for x1 legacy NAS5 vs canonical /nas CIFS routing."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/hermes-rootless/verify_nas5_route.py"
SPEC = importlib.util.spec_from_file_location("verify_nas5_route", SCRIPT)
assert SPEC and SPEC.loader
route = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(route)

EXPECTED = "/nas autofs systemd-1\n/nas cifs //192.168.1.202/fileserver\n"
GOOD = dict(canonical_is_mount=True, legacy_is_mount=False,
            root_dev=1, canonical_dev=80, legacy_dev=1,
            legacy_bind_present=True)

class Nas5RouteReconciliationTests(unittest.TestCase):
    def test_matches_fleet_inventory_and_rejects_legacy_bind(self):
        r = route.classify(EXPECTED, **GOOD)
        self.assertTrue(r["canonical_nas_cifs_matches_fleet_inventory"])
        self.assertTrue(r["legacy_bind_misses_canonical_cifs"])
        self.assertFalse(r["legacy_nas5_exact_mountpoint"])
        self.assertIn("legacy_bind_misses_verified_cifs_route", r["failures"])
        self.assertEqual(r["decision"], "HOLD")
        self.assertFalse(r["production_deployment_authorized"])
    def test_autofs_alone_not_cifs(self):
        r = route.classify("/nas autofs systemd-1\n", **GOOD)
        self.assertFalse(r["canonical_nas_cifs_matches_fleet_inventory"])
        self.assertIn("canonical_nas_route_unverified", r["failures"])
    def test_wrong_remote_share_rejected(self):
        r = route.classify("/nas cifs //192.168.1.99/fileserver\n", **GOOD)
        self.assertFalse(r["canonical_nas_cifs_matches_fleet_inventory"])
    def test_duplicate_expected_cifs_rejected(self):
        r = route.classify(EXPECTED + EXPECTED.splitlines()[-1] + "\n", **GOOD)
        self.assertFalse(r["canonical_nas_cifs_matches_fleet_inventory"])
    def test_rogue_secondary_cifs_rejected(self):
        r = route.classify(EXPECTED + "/nas cifs //192.168.1.99/other\n", **GOOD)
        self.assertFalse(r["canonical_nas_cifs_matches_fleet_inventory"])
    def test_root_device_fallback_rejected(self):
        r = route.classify(EXPECTED, **dict(GOOD, canonical_dev=1))
        self.assertFalse(r["canonical_nas_cifs_matches_fleet_inventory"])
    def test_bare_legacy_mount_not_authorized(self):
        r = route.classify(EXPECTED, **dict(GOOD, legacy_is_mount=True, legacy_dev=88))
        self.assertIn("legacy_path_mount_identity_not_authorized", r["failures"])
        self.assertFalse(r["production_knowledge_mount_authorized"])
    def test_live_legacy_bind_absent_is_not_a_deployment_grant(self):
        r = route.classify(EXPECTED, **dict(GOOD, legacy_bind_present=False))
        self.assertFalse(r["legacy_bind_misses_canonical_cifs"])
        self.assertFalse(r["remote_share_custody_independently_attested"])
        self.assertFalse(r["production_deployment_authorized"])
    def test_invalid_untrusted_records_denied(self):
        for data, attrs in (
            (True, GOOD), (EXPECTED, dict(GOOD, canonical_dev=True)),
            (EXPECTED, dict(GOOD, legacy_bind_present="true")),
            ("a"*6000, GOOD),
        ):
            with self.subTest(data=str(data)[:20]), self.assertRaises(ValueError):
                route.classify(data, **attrs)
    def test_default_no_physical_ops_and_no_approval(self):
        with patch.object(route, "physical_readonly") as physical:
            value = route.default_hold()
        physical.assert_not_called()
        self.assertFalse(value["production_deployment_authorized"])
        self.assertFalse(value["physical_metadata_executed"])
    def test_report_redacts_endpoints_and_paths(self):
        r = route.classify(EXPECTED, **GOOD)
        report = str(r)
        for prohibited in ("/media/scott", "//192.168.", "FLEET_ROUTER_BEARER_TOKEN"):
            self.assertNotIn(prohibited, report)

if __name__ == "__main__":
    unittest.main()
