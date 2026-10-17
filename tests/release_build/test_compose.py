import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("canary_compose", ROOT / "scripts/verify-canary-compose.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ComposeContractTests(unittest.TestCase):
    def setUp(self):
        self.model = {
            "name": "assistx-canary",
            "networks": {"default": {"name": "assistx_canary_default"}},
            "volumes": {"state": {"name": "assistx-canary_state"}},
            "services": {},
        }
        for name in ("api", "worker", "hermes-adapter", "redis", "neo4j"):
            self.model["services"][name] = {
                "container_name": f"assistx-canary-{name}",
                "networks": {"default": {}},
                "volumes": [{"type": "volume", "source": "state", "target": "/app/state"}],
                "environment": {
                    "FLEET_UNSAFE_SHELL_TASKS_ENABLED": "false",
                    "HERMES_SELFTASK_ENABLED": "false",
                    "REDIS_URL": "redis://redis:6379/0",
                    "ASSISTX_URL": "http://api:8000",
                },
            }
        self.model["services"]["api"]["ports"] = [{"host_ip": "127.0.0.1", "target": 8000}]
        self.model["services"]["hermes-adapter"]["profiles"] = ["canary-executor"]

    def test_isolated_model(self):
        self.assertEqual(module.validate(self.model), [])

    def test_production_authority_regressions(self):
        mutations = [
            ("volumes", [{"type": "bind", "source": "/home/scott/git", "target": "/repos"}]),
            ("networks", {"default": {}, "git_default": {}}),
            ("container_name", "assistx-api"),
            ("ports", [{"host_ip": "0.0.0.0", "target": 8000}]),
            ("privileged", True),
            ("pid", "host"),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                model = copy.deepcopy(self.model)
                model["services"]["api"][key] = value
                self.assertTrue(module.validate(model))

    def test_service_dns_and_mutation_flags(self):
        for key, value in [("REDIS_URL", "redis://assistx-redis:6379/0"), ("ASSISTX_URL", "http://assistx-api:8000"), ("HERMES_SELFTASK_ENABLED", "true")]:
            with self.subTest(key=key):
                model = copy.deepcopy(self.model)
                model["services"]["worker"]["environment"][key] = value
                self.assertTrue(module.validate(model))

    def test_external_volume_rejected(self):
        self.model["volumes"]["state"]["external"] = True
        self.assertTrue(module.validate(self.model))

    def test_default_executor_start_rejected(self):
        self.model["services"]["hermes-adapter"].pop("profiles")
        self.assertTrue(module.validate(self.model))
