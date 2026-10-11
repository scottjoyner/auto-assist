"""Synthetic procfs/socket-table lineage tests; no live listeners or traffic."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "collect_lmstudio_worker_lineage",
    ROOT / "scripts" / "collect_lmstudio_worker_lineage.py",
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)
BOOT = "d867483a-d299-49d0-ac3a-7fa4a7d41c1a"


class TestLmStudioLineage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.boot = self.root / "boot_id"
        self.boot.write_text(BOOT + "\n")
        self.tcp = self.root / "tcp"
        self.tcp6 = self.root / "tcp6"
        self.tcp6.write_text("header\n")
        self.proc_entry(1535, "lm-studio", 1211, 1125, 37579)
        self.proc_entry(4783, "llama-server", 1535, 2340, 145600)
        self.network()

    def proc_entry(self, pid, name, parent, ticks, inode):
        root = self.proc / str(pid)
        root.mkdir(exist_ok=True)
        (root / "comm").write_text(name + "\n")
        # /proc/PID/stat fields 3 (state), 4 (ppid), 22 (starttime).
        fields = ["S", str(parent)] + ["0"] * 17 + [str(ticks), "0"]
        (root / "stat").write_text(
            f"{pid} (misleading )) process name) " + " ".join(fields) + "\n"
        )
        fd = root / "fd"
        fd.mkdir()
        (fd / "7").symlink_to(f"socket:[{inode}]")

    def network(self, gateway_inode=37579, worker_inode=145600):
        rows = ["sl local_address rem_address st tx_queue rx_queue tr "
                "tm->when retrnsmt uid timeout inode"]
        for rank, (port, inode) in enumerate(
            ((1234, gateway_inode), (45435, worker_inode))
        ):
            rows.append(
                f"{rank}: 0100007F:{port:04X} 00000000:0000 0A "
                f"00000000:00000000 00:00000000 00000000 1000 0 {inode} 1"
            )
        self.tcp.write_text("\n".join(rows) + "\n")

    def observe(self, **extra):
        args = dict(
            gateway_pid=1535, worker_pid=4783,
            gateway_port=1234, worker_port=45435,
            proc_root=self.proc, tcp_tables=(self.tcp, self.tcp6),
            boot_id_path=self.boot, health_reader=lambda _: True,
        )
        args.update(extra)
        return M.observe(**args)

    def test_honest_parent_child_and_socket_lineage(self):
        result = self.observe()
        self.assertEqual(result["worker_parent_pid"], 1535)
        self.assertEqual(result["gateway_process_start_ticks"], 1125)
        self.assertEqual(result["worker_process_start_ticks"], 2340)
        self.assertEqual(result["worker_listener_port"], 45435)
        self.assertEqual(result["boot_id"], BOOT)
        self.assertTrue(result["listener_ownership_observed"])
        self.assertTrue(result["direct_worker_health_ok"])

    def test_no_signature_or_gateway_request_receipt_is_claimed(self):
        out = self.observe()
        for field in (
            "gateway_forwarding_attested", "source_signature_verified",
            "owner_bound_request_attested", "can_approve", "can_dispatch",
            "physical_model_digest_checked_by_this_tool",
            "model_loaded_inference_attested", "operator_approval_present",
        ):
            self.assertFalse(out[field], field)

    def test_wrong_child_parent_is_rejected(self):
        self.proc_entry(5000, "llama-server", 1, 2340, 145601)
        with self.assertRaisesRegex(ValueError, "not an LM Studio child"):
            self.observe(worker_pid=5000)

    def test_gateway_wrong_executable_denied(self):
        (self.proc / "1535" / "comm").write_text("python3\n")
        with self.assertRaisesRegex(ValueError, "executable"):
            self.observe()

    def test_worker_socket_not_owned_refused(self):
        self.network(worker_inode=998877)
        with self.assertRaisesRegex(ValueError, "direct listener"):
            self.observe()

    def test_gateway_listener_owned_by_another_process_refused(self):
        self.network(gateway_inode=885533)
        with self.assertRaisesRegex(ValueError, "API listener"):
            self.observe()

    def test_established_socket_does_not_count_as_listener(self):
        self.tcp.write_text(self.tcp.read_text().replace(" 0A ", " 01 "))
        with self.assertRaisesRegex(ValueError, "API listener"):
            self.observe()

    def test_socket_fd_replaced_during_health_refused(self):
        def mutate(_):
            (self.proc / "4783" / "fd" / "7").unlink()
            (self.proc / "4783" / "fd" / "7").symlink_to("socket:[5555]")
            return True
        with self.assertRaisesRegex(ValueError, "changed"):
            self.observe(health_reader=mutate)

    def test_pid_start_ticks_changed_during_health_refused(self):
        def mutate(_):
            value = (self.proc / "4783" / "stat").read_text()
            (self.proc / "4783" / "stat").write_text(value.replace("2340", "2341"))
            return True
        with self.assertRaisesRegex(ValueError, "changed"):
            self.observe(health_reader=mutate)

    def test_invalid_boot_uuid_refused(self):
        self.boot.write_text("unsigned-invented-host")
        with self.assertRaises(ValueError):
            self.observe()

    def test_health_fail_is_observation_not_source_attestation(self):
        result = self.observe(health_reader=lambda _: False)
        self.assertFalse(result["direct_worker_health_ok"])
        self.assertFalse(result["can_dispatch"])

    def test_no_private_urls_or_process_args_appear_in_record(self):
        raw = json.dumps(self.observe())
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn("http://", raw)
        self.assertNotIn("token", raw.lower())

    def test_unrelated_listener_no_inference_of_affinity(self):
        other = self.proc / "1006"
        other.mkdir()
        self.network()
        res = self.observe()
        self.assertFalse(res["gateway_forwarding_attested"])

    def test_wrong_listener_port_refused(self):
        with self.assertRaisesRegex(ValueError, "direct listener"):
            self.observe(worker_port=1235)

    def test_process_stat_handles_nested_parenthesis(self):
        self.assertEqual(M.proc_identity(self.proc, 4783)[2], 2340)


if __name__ == "__main__":
    unittest.main()
