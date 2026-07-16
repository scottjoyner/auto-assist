"""Run the verbatim return-contract chain demo against the real adapter routing.

This exercises the full ingest -> delegate -> assign path with a mocked
opencode-cli child (no live AssistX/Neo4j/opencode needed) and asserts the
delegated verbatim value lands in the completed task's result.output.
"""
import importlib.util
import os

import pytest

_DEMO = os.path.join(os.path.dirname(__file__), "..", "examples", "verbatim_chain_demo.py")


@pytest.fixture
def demo_module():
    spec = importlib.util.spec_from_file_location("verbatim_chain_demo", _DEMO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_verbatim_chain_demo(demo_module):
    # main() sets up env, mocks the opencode-cli child, runs process_task, and
    # asserts the verbatim token reaches result.output. Returns 0 on success.
    assert demo_module.main() == 0
