import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("runtime_image", ROOT / "scripts/verify-runtime-image.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RuntimeImageTests(unittest.TestCase):
    def test_isolated_pins(self):
        module.verify({"prefix": "/usr/local", "dotenv": "1.0.1", "pydantic": "2.8.2"}, {"prefix": "/opt/hermes", "version": "0.19.0"})

    def test_shared_interpreter_rejected(self):
        with self.assertRaises(ValueError):
            module.verify({"prefix": "/usr/local"}, {"prefix": "/usr/local"})

    def test_overwritten_application_pin_rejected(self):
        with self.assertRaises(ValueError):
            module.verify({"prefix": "/usr/local", "dotenv": "1.2.2", "pydantic": "2.8.2"}, {"prefix": "/opt/hermes", "version": "0.19.0"})
