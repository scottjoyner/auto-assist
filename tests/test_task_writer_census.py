"""Static writer census negatives; does not import the AssistX application."""
from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_task_writer_sites.py"
AUDIT = runpy.run_path(str(SCRIPT))


def _inspect(tmp_path: Path, text: str):
    f = tmp_path / "sample.py"
    f.write_text(text)
    return AUDIT["inspect"](f, tmp_path)


def test_tracks_static_task_writes_and_reads_without_query_payload(tmp_path):
    sites = _inspect(tmp_path, '''
def update(tx):
    tx.run("MATCH (t:Task {id:$id}) SET t.status='DONE'")
def inspect(tx):
    tx.run("MATCH (t:Task {id:$id}) RETURN t.status")
def other(tx):
    tx.run("MATCH (n:Node) SET n.status='DONE'")
''')
    assert [x["kind"] for x in sites] == [
        "task_write_candidate", "task_read", "other_graph_write",
    ]
    assert all("query" not in x or x["query"] is None for x in sites)
    assert all(x["file_sha12"] and x["path"] == "sample.py" for x in sites)


def test_dynamic_query_and_interpolation_never_admitted_as_covered(tmp_path):
    sites = _inspect(tmp_path, '''
def unknown(tx, q, user):
    tx.run(q)
    tx.run(f"MATCH (t:Task {{id:'{user}'}}) SET t.status='DONE'")
def composed(tx):
    q = "MATCH (t:Task) " + "SET t.status='DONE'"
    tx.run(q)
''')
    assert [x["kind"] for x in sites] == [
        "dynamic_query", "dynamic_query", "task_write_candidate",
    ]
    assert sites[1]["confidence"] == "unresolved"
    assert sites[2]["confidence"] == "candidate"


def test_callbacks_and_non_graph_subprocess_run(tmp_path):
    sites = _inspect(tmp_path, '''
import subprocess
def run(session):
    subprocess.run(["echo","safe"])
    session.execute_write(lambda tx: tx.run("MATCH (t:Task) DELETE t"))
''')
    assert [x["kind"] for x in sites] == [
        "transaction_callback", "task_write_candidate",
    ]


def test_syntax_error_is_unresolved_not_silently_skipped(tmp_path):
    sites = _inspect(tmp_path, "def invalid(:\n    pass\n")
    assert len(sites) == 1
    assert sites[0]["kind"] == "parse_error"
    assert sites[0]["confidence"] == "unresolved"


def test_git_scoped_report_stays_hold_with_dynamic_writers(tmp_path):
    root = tmp_path
    (root / "src").mkdir()
    (root / "src" / "writer.py").write_text(
        'def work(tx, query):\n    tx.run(query)\n'
    )
    (root / "src" / "tests").mkdir()
    (root / "src" / "tests" / "test_fake.py").write_text(
        "def fake(tx):\n    tx.run('MATCH (t:Task) DELETE t')\n"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Research Test",
                    "-c", "user.email=test@localhost.invalid",
                    "commit", "-qm", "test fixture"], check=True)
    report = AUDIT["build"](root)
    assert report["decision"] == "HOLD"
    assert report["source_authority_complete"] is False
    assert report["production_dispatch_authorized"] is False
    assert report["site_count"] == 1
    assert report["unresolved_sites"] == 1
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(root),
         "--fail-on-unresolved", "--summary-only"],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["unresolved_sites"] == 1


def test_real_revision_inventory_has_unresolved_and_no_dispatch_authority():
    report = AUDIT["build"](Path(__file__).resolve().parents[1])
    assert report["decision"] == "HOLD"
    assert report["source_authority_complete"] is False
    assert report["production_dispatch_authorized"] is False
    assert report["kind_counts"]["task_write_candidate"] > 0
    assert report["unresolved_sites"] > 0
    assert any(site["function"] == "claim_task" for site in report["sites"])
