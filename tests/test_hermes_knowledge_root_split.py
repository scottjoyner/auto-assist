"""Opt-in separate Hermes RO input and RW output contract (synthetic only)."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from assistx.agents.hermes_knowledge_ro import (
    ReadScopeDenied, bounded_snapshot, validate_separate_roots,
)
from assistx.agents import hermes_agent_adapter as adapter


class HermesSplitRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-split-ro-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.read = self.base / "read"
        self.write = self.base / "write"
        self.read.mkdir()
        self.write.mkdir()
        (self.read / "source.md").write_text("SYNTHETIC SOURCE ONLY\n")
        (self.read / "notes").mkdir()
        (self.read / "notes" / "context.txt").write_text("synthetic nested context\n")

    def test_distinct_knowledge_and_workspace(self):
        text = bounded_snapshot(str(self.read), str(self.write))
        self.assertIn("SYNTHETIC SOURCE ONLY", text)
        self.assertIn("synthetic nested context", text)
        self.assertFalse(any(self.write.iterdir()))

    def test_overlap_or_ancestor_denied(self):
        for dest in (self.read, self.read/"child", self.base):
            with self.subTest(dest=dest), self.assertRaises(ReadScopeDenied):
                bounded_snapshot(str(self.read),str(dest))

    def test_symlinked_root_denied(self):
        alias=self.base/"alias"
        alias.symlink_to(self.read, target_is_directory=True)
        with self.assertRaises(ReadScopeDenied):
            bounded_snapshot(str(alias),str(self.write))

    def test_unsafe_file_links_not_read(self):
        (self.read/"outside.md").symlink_to(self.write/"private.md")
        (self.write/"private.md").write_text("DENIED_SENTINEL")
        output=bounded_snapshot(str(self.read),str(self.write))
        self.assertNotIn("DENIED_SENTINEL",output)
        self.assertNotIn("outside.md",output)

    def test_hidden_credential_names_excluded(self):
        (self.read/"token_notes.txt").write_text("DENIED_TOKEN")
        (self.read/".secret.md").write_text("DENIED_SECRET")
        out=bounded_snapshot(str(self.read),str(self.write))
        self.assertNotIn("DENIED_TOKEN",out)
        self.assertNotIn("DENIED_SECRET",out)

    def test_hardlinked_file_is_excluded(self):
        file=self.write/"provenance.md"
        file.write_text("HARDLINK_DENIED")
        os.link(file,self.read/"hardlink.md")
        self.assertNotIn("HARDLINK_DENIED",bounded_snapshot(str(self.read),str(self.write)))

    def test_bounded_context_and_filecount(self):
        out=bounded_snapshot(str(self.read),str(self.write),max_chars=14)
        self.assertLessEqual(len(out),14)
        with self.assertRaises(ReadScopeDenied):
            bounded_snapshot(str(self.read),str(self.write),max_chars=9000)
        for i in range(35):
            (self.read/f"synthetic{i}.md").write_text("source")
        with self.assertRaises(ReadScopeDenied):
            bounded_snapshot(str(self.read),str(self.write))

    def test_invalid_opt_in_path_never_falls_back_to_writable_knowledge(self):
        with patch.dict(os.environ,{"HERMES_KNOWLEDGE_READ_ROOT":str(self.read)}):
            with patch.object(adapter,"KNOWLEDGE_ROOT",str(self.write)):
                with self.assertRaises(ReadScopeDenied):
                    adapter._gather_knowledge_context()

    def test_adapter_exact_readonly_mount_dispatch_to_bounded_reader(self):
        with patch.dict(os.environ,{"HERMES_KNOWLEDGE_READ_ROOT":"/research/knowledge"}):
            with patch.object(adapter,"KNOWLEDGE_ROOT",str(self.write)):
                with patch.object(adapter,"bounded_snapshot",return_value="synthetic read") as reader:
                    self.assertEqual(adapter._gather_knowledge_context(),"synthetic read")
                    reader.assert_called_once_with("/research/knowledge",str(self.write),max_chars=2500)

    def test_ensure_model_env_stays_in_private_write_workspace(self):
        with patch.dict(os.environ,{"HERMES_KNOWLEDGE_ROOT":str(self.write)}):
            with patch.object(adapter,"EVAL_PATH",str(self.write/"models.json")):
                with patch.object(adapter,"load_eval",return_value={"models":{}}):
                    with patch.object(adapter,"save_eval"):
                        adapter.ensure_model_env("synthetic/model")
        private=list(self.write.rglob("ENV.md"))
        self.assertEqual(len(private),1)
        self.assertFalse(list(self.read.rglob("ENV.md")))
        self.assertNotIn("/media/scott/SSD_4TB",private[0].read_text())

    def test_invalid_split_root_denied_before_first_task_poll(self):
        with patch.dict(os.environ,{"HERMES_KNOWLEDGE_READ_ROOT":"/media/scott/NAS5"}):
            with patch.object(adapter,"AssistXClient") as client:
                with self.assertRaises(ReadScopeDenied):
                    adapter.run_loop(once=True)
                client.assert_not_called()

    def test_compose_source_and_write_mounts_distinct(self):
        yaml=(Path(__file__).resolve().parents[1]/"deploy/hermes-rootless/compose.synthetic.yml").read_text()
        self.assertIn("HERMES_KNOWLEDGE_READ_ROOT: /research/knowledge",yaml)
        self.assertIn("HERMES_KNOWLEDGE_ROOT: /workspace/knowledge",yaml)
        self.assertIn("read_only: true",yaml)
        self.assertIn("create_host_path: false",yaml)

    def test_opt_in_semantic_memory_bypass_disabled(self):
        source=(Path(__file__).resolve().parents[1]/"src/assistx/agents/hermes_agent_adapter.py").read_text()
        self.assertIn('swarm_memory is not None and "HERMES_KNOWLEDGE_READ_ROOT" not in os.environ',source)

if __name__ == "__main__":
    unittest.main()
