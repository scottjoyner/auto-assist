"""Negative contract checks for one-shot rootless Hermes synthetic task fixture.

The fixture must NEVER be baked into a release image or enabled by default.
"""
from pathlib import Path
import unittest

FIXTURE=Path(__file__).resolve().parent/"fixtures/hermes_rootless_synthetic_lifecycle.py"
COMPOSE=Path(__file__).resolve().parents[1]/"deploy/hermes-rootless/compose.synthetic.yml"

class RootlessMainSyntheticContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=FIXTURE.read_text()
        compile(cls.source,str(FIXTURE),"exec")
    def test_main_executes_with_bounded_one_poll(self):
        s=self.source
        self.assertIn("entry.main()",s)
        self.assertIn("original_run_loop(once=True)",s)
        self.assertIn('patch.object(agent, "run_loop", side_effect=bounded_single_poll)',s)
        self.assertIn('assert getattr(agent, "_strict_executor_auth_installed", False)',s)
    def test_unreal_network_refused_and_requests_mocked(self):
        s=self.source
        self.assertIn('patch("requests.request", side_effect=http)',s)
        self.assertIn('http://127.0.0.1:1/',s)
        self.assertIn('assert url.startswith("http://127.0.0.1:1/")',s)
        self.assertIn('patch.object(subprocess, "Popen", side_effect=forbid_process)',s)
        self.assertNotIn("socket.socket(",s)
    def test_synthetic_credentials_only(self):
        s=self.source
        self.assertIn("Bearer SYNTHETIC_BOOTSTRAP_ONLY",s)
        self.assertIn("Bearer SYNTHETIC_CLAIM_ONLY",s)
        self.assertNotIn("OPENAI_API_KEY",s)
        self.assertNotIn("FLEET_ROUTER_BEARER_TOKEN",s)
    def test_scope_and_lease_negative_controls(self):
        s=self.source
        self.assertIn('client._lease_lost.set()',s)
        self.assertIn('assert events.count("complete") == pre_complete',s)
        self.assertIn('assert client.task_token == ""',s)
        self.assertIn('"different claim identity"',s)
        self.assertIn("CapEff:",s)
        self.assertIn("/research/knowledge/forbidden",s)
    def test_no_real_agent_enabled_by_compose(self):
        config=COMPOSE.read_text()
        self.assertIn('command: ["/bin/sh", "/workspace/synthetic_smoke.sh"]',config)
        self.assertNotIn('command: ["python3"',config)
        self.assertIn("network_mode: none",config)
        self.assertIn("pull_policy: never",config)

if __name__=="__main__":
    unittest.main()
