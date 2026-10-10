import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

script = Path(__file__).resolve().parents[1] / "scripts" / "inventory_trace_headers.py"
spec = importlib.util.spec_from_file_location("inventory", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class InventoryTests(unittest.TestCase):
    def test_metadata_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            row = {"session_id":"abc-personal-session", "parent_session_id":"p",
                   "tokens":{"input":5}, "messages":{"user":"DO_NOT_OUTPUT_MESSAGE"}}
            (root / "sample.jsonl").write_text(json.dumps(row) + "\n")
            result = module.inventory([root])
            self.assertEqual(result["rows_parsed"], 1)
            self.assertEqual(result["unique_sessions_observed"], 1)
            self.assertNotIn("DO_NOT_OUTPUT_MESSAGE", json.dumps(result))
            self.assertNotIn("abc-personal-session", json.dumps(result))

    def test_bad_json_and_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root / "a.jsonl").write_text('{"session_id":"x"}\nnot-valid\n')
            (root / "b.jsonl").symlink_to(root / "a.jsonl")
            report=module.inventory([root])
            self.assertEqual(report["files_inspected"],1)
            self.assertEqual(report["rows_parsed"],1)
            self.assertEqual(report["rows_malformed"],1)

if __name__=="__main__":
    unittest.main()
