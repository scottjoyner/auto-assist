"""Conservative offline Neo4j migration inventory checks (no network)."""
from pathlib import Path

import pytest

from trace_graph_consumer_census import inventory


def make_fixture(tmp_path: Path):
    root = tmp_path / "assistx"
    root.mkdir()
    (root / "neo4j_client.py").write_text("""
class Neo4jClient:
    def get_task(self, task_id): pass
    def upsert_ticket(self, title): pass
    def close(self): pass
    def _session(self): pass
""")
    (root / "api.py").write_text("""
def route():
    neo = load_neo()
    neo.get_task("t")
    neo.upsert_ticket("title")
    neo.close()
    with neo._session() as session:
        session.run("MATCH (n) RETURN n")
""")
    (root / "worker.py").write_text("""
def work(client):
    return client.get_task("t")
""")
    (root / "tests").mkdir()
    (root / "tests" / "test_fake.py").write_text("""
def test_a(neo):
    neo.upsert_ticket("should be excluded")
""")
    return root


def test_inventory_records_broker_migrations_and_direct_sessions(tmp_path):
    result = inventory(make_fixture(tmp_path))
    assert result["neo4j_client_declared_methods"] == 4
    assert result["potential_graph_method_calls"] == 3
    assert result["direct_session_calls_outside_client"] == 1
    assert result["files_with_evidence"] == 2
    assert "get_task" in {
        x["method"] for x in result["task_policy_calls"]
    }
    assert {"get_task", "upsert_ticket"} <= {
        x["method"] for x in result["highest_method_calls"]
    }
    assert result["direct_session_calls"] == [{
        "file": "api.py", "function": "route", "line": 7,
        "symbol": "neo._session", "class": "DIRECT_SESSION_REVIEW",
    }]
    assert not result["interpretation"]["not_runtime_call_graph"] is False
    assert result["release_gate"].startswith("B02_UNACCEPTED")
    assert not any("tests/test_fake.py" in str(x)
                   for x in result["task_policy_calls"])


def test_missing_or_wrong_client_source_rejected(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    with pytest.raises(ValueError, match="MISSING_NEO4J_CLIENT"):
        inventory(root)
    (root / "neo4j_client.py").write_text("class WrongClient: pass")
    with pytest.raises(ValueError, match="NEO4J_CLIENT_CLASS_NOT_UNIQUE"):
        inventory(root)


def test_current_repo_has_multiple_direct_session_unmigrated_consumers():
    root = Path(__file__).resolve().parents[1] / "src" / "assistx"
    result = inventory(root)
    assert result["direct_session_calls_outside_client"] >= 10
    assert result["potential_graph_method_calls"] >= 10
    direct_files = {row["file"] for row in result["direct_session_calls"]}
    assert "api.py" in direct_files
    assert "voice_routes.py" in direct_files
    assert result["interpretation"]["not_complete_dynamic_alias_analysis"]


def test_inventory_no_runtime_connectors_or_prod_credentials():
    code = Path(__file__).with_name("trace_graph_consumer_census.py").read_text()
    for banned in ("import neo4j", "GraphDatabase.driver(",
                   "os.environ", "subprocess.", "docker ", "urllib.request"):
        assert banned not in code


def test_nine_task_lookup_consumers_remain_separately_unmigrated():
    root = Path(__file__).resolve().parents[1] / "src" / "assistx"
    result = inventory(root)
    sites = {(row["file"], row["function"]) for row in
             result["task_policy_calls"] if row["method"] == "get_task"}
    assert {
        ("api.py", "api_claim_task"),
        ("api.py", "api_heartbeat_task"),
        ("api.py", "api_complete_task"),
        ("api.py", "api_workflow_replan"),
        ("api.py", "api_workflow_budget_update"),
        ("routers/review.py", "api_approve_review_task"),
        ("routers/review.py", "api_reject_review_task"),
        ("routers/review.py", "api_clarify_review_task"),
    } <= sites
    # Two get_task calls exist inside complete_task; don't equate distinct
    # call sites with unique functions or endpoint counts.
    assert len([x for x in result["task_policy_calls"]
                if x["method"] == "get_task"]) >= 9


def test_review_requires_title_and_completion_needs_full_task_semantics():
    root = Path(__file__).resolve().parents[1] / "src" / "assistx"
    review = (root / "routers/review.py").read_text()
    api = (root / "api.py").read_text()
    assert 'review_task.get("title")' in review
    assert 'review_task.get("payload_json")' in review
    assert "evaluate_completion(" in api
    assert "improvement_evaluation" in api
    # The claim-guard projector from PR #296 intentionally has no title.
    broker = Path(__file__).with_name(
        "trace_disposable_task_broker.py").read_text()
    assert '"payload", "payload_json", "queue_class"' in broker
    assert '".title"' not in broker
    full_review = {"status": "REVIEW", "title": "Important review",
                   "payload_json": '{"source_text":""}'}
    claim_only = {k: v for k, v in full_review.items()
                  if k in {"status", "payload_json"}}
    assert full_review["title"] != claim_only.get("title", "")
