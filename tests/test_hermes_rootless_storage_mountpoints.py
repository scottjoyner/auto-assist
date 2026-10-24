"""Offline negative tests for metadata-only NAS/SSD mountpoint witness."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest

FILE=Path(__file__).resolve().parents[1]/"deploy/hermes-rootless/verify_storage_mountpoints.py"
SPEC=importlib.util.spec_from_file_location("verify_storage_mountpoints", FILE)
assert SPEC and SPEC.loader
gate=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

class StorageMetadataGateTests(unittest.TestCase):
    def test_not_requested_is_no_go(self):
        with patch.object(gate, "physical_probe") as physical:
            result=gate.default_hold()
        physical.assert_not_called()
        self.assertEqual(result["decision"],"HOLD")
        self.assertFalse(result["metadata_topology_gate_pass"])

    def test_correct_distinct_mounts_are_not_deployment_authority(self):
        out=gate.verdict(nas_mounted=True,ssd_mounted=True,
                         root_dev=1,nas_dev=2,ssd_dev=3)
        self.assertTrue(out["metadata_topology_gate_pass"])
        self.assertFalse(out["production_deployment_authorized"])
        self.assertFalse(out["production_data_read_or_modified"])
        self.assertEqual(out["decision"],"HOLD")
        self.assertEqual(out["failures"],[])

    def test_missing_nas_mount_denied(self):
        out=gate.verdict(nas_mounted=False,ssd_mounted=True,
                         root_dev=1,nas_dev=1,ssd_dev=3)
        self.assertFalse(out["metadata_topology_gate_pass"])
        self.assertIn("nas5_not_exact_mountpoint",out["failures"])
        self.assertIn("nas5_on_host_root_filesystem",out["failures"])

    def test_missing_ssd_mount_denied(self):
        out=gate.verdict(nas_mounted=True,ssd_mounted=False,
                         root_dev=1,nas_dev=2,ssd_dev=1)
        self.assertFalse(out["metadata_topology_gate_pass"])
        self.assertIn("ssd_not_exact_mountpoint",out["failures"])
        self.assertIn("ssd_on_host_root_filesystem",out["failures"])

    def test_shared_device_denied(self):
        out=gate.verdict(nas_mounted=True,ssd_mounted=True,
                         root_dev=1,nas_dev=2,ssd_dev=2)
        self.assertFalse(out["metadata_topology_gate_pass"])
        self.assertIn("nas5_ssd_share_device",out["failures"])

    def test_invalid_metadata_denied(self):
        for kw in [
            {"nas_mounted":1,"ssd_mounted":True,"root_dev":1,"nas_dev":2,"ssd_dev":3},
            {"nas_mounted":True,"ssd_mounted":True,"root_dev":True,"nas_dev":2,"ssd_dev":3},
            {"nas_mounted":True,"ssd_mounted":True,"root_dev":1,"nas_dev":-2,"ssd_dev":3},
        ]:
            with self.subTest(**kw),self.assertRaises(ValueError):
                gate.verdict(**kw)

    def test_output_has_no_data_contents_or_host_paths(self):
        out=gate.verdict(nas_mounted=False,ssd_mounted=True,
                         root_dev=1,nas_dev=1,ssd_dev=3)
        result=str(out)
        for secret in ("/home/scott", "/media/scott", "FLEET_ROUTER_BEARER_TOKEN"):
            self.assertNotIn(secret,result)

if __name__=="__main__":
    unittest.main()
