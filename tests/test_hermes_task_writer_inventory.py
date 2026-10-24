"""Offline static source inventory regression; no fleet/Neo4j writes."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/hermes_task_writer_inventory.py"
spec=importlib.util.spec_from_file_location("hermes_task_writer_inventory",SCRIPT)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_guarded_and_unguarded_writers_are_visible():
    source='''
def a(s):
    s.run("""MATCH (t:Task{id:$id}) SET t.status='DONE'""")
def b(s):
    s.run("""MATCH (t:Task{id:$id})
       SET t.hermes_fence_check_epoch=coalesce(t.hermes_fence_check_epoch,0)+1
       WITH t
       WHERE t.status='READY'
       SET t.status='CLAIMED'""")
def c(s):
    s.run("""CREATE (t:Task{id:'synthetic'})""")
def d(s):
    s.run("""MATCH (t:Task) DETACH DELETE t""")
'''
    result=module.inspect_source(source,"synthetic.py")
    assert len(result["writers"])==4
    assert [item["kind"] for item in result["writers"]]==[
        "task_property_mutation","task_property_mutation",
        "task_node_create_or_merge","task_delete",
    ]
    assert [item["structural_lock_marker"] for item in result["writers"]]==[
        False,True,False,False,
    ]
    assert all(not v["generation_protocol_certified"] for v in result["writers"])


def test_concatenated_literals_discovered_without_query_text_leak():
    src='''
def writer(s):
    s.run("MATCH (t:Task{id:$id}) " "SET t.paused=true")
'''
    r=module.inspect_source(src,"synthetic.py")
    assert len(r["writers"])==1
    assert "MATCH" not in str(r["writers"])


def test_dynamic_queries_counted_as_unknown_not_certified():
    src='''
def dynamic(s, query):
    s.run(query)
    s.run(f"MATCH (t:Task{{id:$id}}) SET t.status='DONE'")
'''
    r=module.inspect_source(src,"synthetic.py")
    assert r["unresolved_dynamic_sql_calls"]==2
    assert r["writers"]==[]


def test_read_only_task_query_not_misclassified():
    src='''
def readonly(s):
    s.run("MATCH (t:Task) RETURN t.id")
'''
    assert module.inspect_source(src,"synthetic.py")["writers"]==[]


def test_fixture_tree_generates_hold_with_uncertified_writers(tmp_path):
    # This is a diagnostic only: never assert it is complete.
    (tmp_path/"worker.py").write_text(
        """def f(s):
    s.run("MATCH (t:Task) SET t.status='READY'")
"""
    )
    r=module.inventory(tmp_path)
    assert r["decision"]=="HOLD"
    assert r["files_scanned"]==1
    assert r["visible_static_task_writer_count"]==1
    assert not r["source_inventory_complete"]
    assert not r["all_writers_generation_fenced"]
    assert not r["production_dispatch_authorized"]
    assert all(set(v)=={"path","line","kind","structural_lock_marker",
                       "generation_protocol_certified"} for v in r["writers"])


def test_unknown_source_denied(tmp_path):
    r=module.inventory(tmp_path/"missing")
    assert r["decision"]=="HOLD"
    assert not r["source_inventory_complete"]
