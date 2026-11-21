"""Pure-Python adversarial Strata preflight tests (no GPU/drivers/process changes)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/experiments/strata_fleet_preflight.py"
spec = importlib.util.spec_from_file_location("strata_fleet_preflight", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def snapshot(dev="0x7551", ram=30000, free=200.0, processes=None, complete=True):
    return {
        "hostname": "research-synthetic",
        "devices": [gate.pci_profile("0x1002",dev)],
        "mem_available_mib": ram,
        "root_available_gib": free,
        "inference_processes": processes if processes is not None else [],
        "process_scan_complete": complete,
    }


class TestStrataPreflight(unittest.TestCase):
    def test_r9700_mapping(self):
        d = gate.pci_profile("0x1002","0x7551")
        self.assertEqual((d["gfx_target"],d["strata_variant"],d["hardware_support"]),
                         ("gfx1201","rx9070","supported"))

    def test_strix_halo_mapping(self):
        d = gate.pci_profile("0x1002","0x1586")
        self.assertEqual((d["gfx_target"],d["strata_variant"],d["hardware_support"]),
                         ("gfx1151","halo","experimental"))

    def test_strix_point_not_supported(self):
        d = gate.pci_profile("0x1002","0x150e")
        self.assertEqual((d["gfx_target"],d["strata_variant"],d["hardware_support"]),
                         ("gfx1150",None,"unsupported"))

    def test_vendor_not_trusted(self):
        d = gate.pci_profile("0x10de","0x7551")
        self.assertIsNone(d["strata_variant"])
        self.assertEqual(d["hardware_support"],"unknown")

    def test_unmapped_amd_is_not_auto_supported(self):
        self.assertEqual(gate.pci_profile("0x1002","0x164e")["hardware_support"],"unknown")

    def test_hardware_positive_never_grants_deploy(self):
        for pci in ("0x7551","0x1586"):
            with self.subTest(pci=pci):
                d = gate.assess(snapshot(dev=pci))
                self.assertEqual(d["precheck_state"],"RESEARCH_PRECHECK_CLEAR")
                self.assertEqual(d["production_admission"],"HOLD")
                self.assertFalse(d["model_or_engine_download_allowed"])
                self.assertFalse(d["service_install_or_restart_allowed"])
                self.assertFalse(d["inference_dispatch_allowed"])
                self.assertFalse(d["actual_inference_benchmark_executed"])
                self.assertIsNotNone(d["candidate_variant"])

    def test_unsupported_never_precheck_clear(self):
        d = gate.assess(snapshot(dev="0x150e"))
        self.assertIn("NO_VERIFIED_SUPPORTED_GPU",d["blockers"])
        self.assertIsNone(d["candidate_variant"])

    def test_inference_live_is_hold(self):
        d = gate.assess(snapshot(processes=["llama-server"]))
        self.assertIn("EXISTING_INFERENCE_PROCESS_ACTIVE",d["blockers"])
        self.assertEqual(d["precheck_state"],"HOLD")

    def test_reserve_ram_hold(self):
        d = gate.assess(snapshot(ram=1204))
        self.assertIn("INSUFFICIENT_OR_UNKNOWN_AVAILABLE_RAM",d["blockers"])

    def test_reserve_disk_hold(self):
        d = gate.assess(snapshot(free=30.0))
        self.assertIn("INSUFFICIENT_OR_UNKNOWN_ROOT_STORAGE",d["blockers"])

    def test_unknown_process_scan_hold(self):
        d = gate.assess(snapshot(complete=False))
        self.assertIn("PROCESS_SCAN_INCOMPLETE",d["blockers"])

    def test_combined_realistic_joyner_snapshot(self):
        d = gate.assess(snapshot(ram=1204,free=286.0,processes=["llama-server"]))
        self.assertEqual(set(d["blockers"]),{
            "INSUFFICIENT_OR_UNKNOWN_AVAILABLE_RAM",
            "EXISTING_INFERENCE_PROCESS_ACTIVE",
        })
        self.assertEqual(d["candidate_variant"],"rx9070")

    def test_combined_realistic_xwing_snapshot(self):
        d = gate.assess(snapshot(dev="0x1586",ram=6728,free=30.0,processes=["llama-server"]))
        self.assertEqual(set(d["blockers"]),{
            "INSUFFICIENT_OR_UNKNOWN_AVAILABLE_RAM",
            "INSUFFICIENT_OR_UNKNOWN_ROOT_STORAGE",
            "EXISTING_INFERENCE_PROCESS_ACTIVE",
        })
        self.assertEqual(d["candidate_variant"],"halo")

    def test_forged_variant_cannot_override_pci(self):
        s = snapshot(dev="0x150e")
        s["devices"][0]["strata_variant"] = "rx9070"
        s["devices"][0]["hardware_support"] = "supported"
        d = gate.assess(s)
        self.assertIsNone(d["candidate_variant"])

    def test_no_unknown_devices_assumed_compatible(self):
        s = snapshot()
        s["devices"] = []
        self.assertIn("NO_VERIFIED_SUPPORTED_GPU",gate.assess(s)["blockers"])

    def test_multi_gpu_prefer_supported_discrete(self):
        s = snapshot(dev="0x1586")
        s["devices"].append(gate.pci_profile("0x1002","0x7551"))
        self.assertEqual(gate.assess(s)["candidate_variant"],"rx9070")

    def test_parse_memavailable_exact(self):
        self.assertEqual(gate.parse_memavailable_mib("MemAvailable: 8192000 kB\n"),8000)
        self.assertIsNone(gate.parse_memavailable_mib("MemFree: 123 kB\n"))

    def test_parse_duplicate_memavailable_denied(self):
        self.assertIsNone(gate.parse_memavailable_mib("MemAvailable: 8000 kB\nMemAvailable: 8200 kB\n"))

    def test_unknown_ram_denied(self):
        self.assertIn("INSUFFICIENT_OR_UNKNOWN_AVAILABLE_RAM",gate.assess(snapshot(ram=None))["blockers"])

    def test_unknown_disk_denied(self):
        self.assertIn("INSUFFICIENT_OR_UNKNOWN_ROOT_STORAGE",gate.assess(snapshot(free=None))["blockers"])

    def test_invalid_process_disallowed(self):
        with self.assertRaises(ValueError):
            gate.assess(snapshot(processes=["malware"]))

    def test_invalid_floors_disallowed(self):
        for ram,disk in ((-1,40),(8192,0),(8192,1001),(True,40)):
            with self.subTest(ram=ram,disk=disk),self.assertRaises(ValueError):
                gate.assess(snapshot(),ram_floor_mib=ram,root_floor_gib=disk)

    def test_empty_ram_not_success(self):
        with self.assertRaises(ValueError):
            gate.assess(snapshot(ram=-1))

    def test_report_contains_no_sensitive_process_command_line(self):
        d = gate.assess(snapshot(processes=["llama-server"]))
        output=json.dumps(d)
        self.assertNotIn("OPENAI_API_KEY",output)
        self.assertNotIn("command_line",output)
        self.assertNotIn("username",output)

    def test_runtime_entry_is_readonly_inspection_only(self):
        source = SOURCE.read_text()
        for forbidden in ("subprocess.run(", "os.system(", "urllib.request", "requests.get(",
                          "socket.connect(", "ssh ", "strata launch", "pip install"):
            self.assertNotIn(forbidden,source)


if __name__ == "__main__":
    unittest.main()
