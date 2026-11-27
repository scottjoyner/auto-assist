"""Separate strict executor must cooperate with normal termination."""
import re
import signal
import unittest
from pathlib import Path
from unittest.mock import patch
from assistx import strict_executor_main as entry

ROOT = Path(__file__).resolve().parents[1]

class ExecutorShutdownContract(unittest.TestCase):
    def test_install_precedes_poll_and_term_reaches_callback(self):
        calls = []
        def install(adapter):
            calls.append("installed")
        def run(*, stopping):
            self.assertEqual(calls, ["installed"])
            self.assertFalse(stopping())
            signal.raise_signal(signal.SIGTERM)
            self.assertTrue(stopping())
            calls.append("draining")
        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(entry, "install_strict_executor_adapter", side_effect=install), patch.object(entry.hermes_agent_adapter, "run_loop", side_effect=run):
            entry.main()
        self.assertEqual(calls, ["installed", "draining"])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_shutdown_during_install_prevents_poll(self):
        with patch.object(entry, "install_strict_executor_adapter", side_effect=lambda adapter: signal.raise_signal(signal.SIGTERM)), patch.object(entry.hermes_agent_adapter, "run_loop") as run:
            entry.main()
            run.assert_not_called()

    def test_install_failure_never_polls(self):
        with patch.object(entry, "install_strict_executor_adapter", side_effect=RuntimeError("fixture")), patch.object(entry.hermes_agent_adapter, "run_loop") as run:
            with self.assertRaises(RuntimeError):
                entry.main()
            run.assert_not_called()

    def test_container_grace_exceeds_default_rq_timeout(self):
        for filename in ("docker-compose.yml", "compose.production.reconciled.yml", "compose.reconciliation.yml"):
            source = (ROOT / filename).read_text()
            for name in ("worker", "hermes-adapter"):
                match = re.search(r"(?ms)^  " + name + r":\n(.*?)(?=^  \S|^\S|\Z)", source)
                self.assertIsNotNone(match, (filename, name))
                section = match.group(1)
                self.assertRegex(section, r"(?m)^    stop_signal: SIGTERM$")
                value = re.search(r"(?m)^    stop_grace_period: (\d+)m$", section)
                self.assertIsNotNone(value, (filename, name))
                self.assertGreaterEqual(int(value.group(1)) * 60, 1800 + 120)
