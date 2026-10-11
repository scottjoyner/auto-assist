"""B02: deterministic, fail-closed graph consumer release inventory guards."""
from __future__ import annotations

import ast
from pathlib import Path
from trace_graph_consumer_inventory_research import scan, render_markdown

ROOT = Path(__file__).resolve().parents[1]


def test_real_source_scanned_and_all_production_consumers_remain_unmigrated():
    result = scan(ROOT)
    assert not result["parse_errors"], result["parse_errors"]
    assert result["static_inventory_only"]
    assert result["safe_to_remove_direct_neo4j"] is False
    assert result["statistics"]["distinct_source_files_with_candidates"] >= 15
    assert result["statistics"]["candidate_graph_call_sites"] >= 60


def test_sensitive_tasks_and_write_operations_cannot_be_misreported_as_safe():
    result = scan(ROOT)
    calls = result["candidate_call_sites"]
    paths = {x["file"] for x in calls}
    assert "src/assistx/api.py" in paths
    assert "src/assistx/fleet_executor.py" in paths
    assert "src/assistx/routers/review.py" in paths
    def has(file, op, kind=None):
        return any(x["file"] == file and x["operation"] == op
                   and (kind is None or x["kind"] == kind) for x in calls)
    assert has("src/assistx/api.py", "get_task", "read")
    assert has("src/assistx/api.py", "claim_task", "write")
    assert has("src/assistx/api.py", "heartbeat_task", "write")
    assert has("src/assistx/api.py", "complete_task", "write")
    assert has("src/assistx/fleet_executor.py", "claim_task", "write")
    assert has("src/assistx/routers/review.py", "get_task", "read")


def test_machine_report_has_stable_line_evidence_and_no_identity_or_secret_values():
    result = scan(ROOT)
    assert all(x["line"] > 0 for x in result["candidate_call_sites"])
    assert all(x["owner"] for x in result["candidate_call_sites"])
    report = render_markdown(result)
    assert "src/assistx/api.py" in report
    assert "**P0 do not migrate blindly** |" in report
    assert "do not" in report.lower()
    assert "candidate" in report.lower()
    assert "NEO4J_PASSWORD=" not in report


def test_inventory_never_loads_live_db_credentials_or_driver_at_runtime():
    source = (ROOT / "tests/trace_graph_consumer_inventory_research.py").read_text()
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert not any(item and (
        item.startswith(("neo4j", "assistx", "docker", "urllib", "requests")))
        for item in imports)
    assert "subprocess" not in source
    assert "source_files_scanned" in source


def test_static_no_query_proof_distinct_from_full_parity():
    result = scan(ROOT)
    assert not result["every_call_fully_resolved"]
    assert not result["live_graph_credential_use_verified"]
    assert result["statistics"]["kinds"]["write"] > 0
    assert result["statistics"]["kinds"]["low_level"] > 0
