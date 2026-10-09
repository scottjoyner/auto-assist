"""Current-main v1 trace export: private, scoped and bounded; no provider calls."""
import importlib.util
import json
import pathlib
import sqlite3
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'scripts/export_opencode_session_traces.py'
SPEC = importlib.util.spec_from_file_location('bounded_v1_trace', SCRIPT)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def db_fixture(tmp_path):
    db=tmp_path/'opencode.db'
    con=sqlite3.connect(db)
    con.executescript('''
        CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT,
            model TEXT, cost REAL, tokens_input INTEGER, tokens_output INTEGER,
            tokens_reasoning INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER,
            time_created INTEGER, time_updated INTEGER);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
    ''')
    for index,title in enumerate(('Trace%exact', 'Trace_exact', 'trace%exact', 'TraceXexact'),start=1):
        con.execute('INSERT INTO session VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (f'ses_{index}',None,str(tmp_path),title,
                     json.dumps({'providerID':'kilo_free','id':'cohere/north-mini-code:free'}),
                     0,10,3,0,0,0,1791543706569+index,1791543706570+index))
    con.commit();con.close();return db


def cli(monkeypatch, db, out, *extra):
    monkeypatch.setattr(sys,'argv',['trace-export','--db',str(db),'--out',str(out),*extra])
    return exporter.main()


def test_session_id_sql_scope_and_repeatable_independent_exports(tmp_path):
    db=db_fixture(tmp_path)
    first=exporter.export_sessions(db,session_id='ses_2',max_sessions=1)
    second=exporter.export_sessions(db,session_id='ses_2',max_sessions=1)
    assert [x['session_id'] for x in first]==[x['session_id'] for x in second]==['ses_2']
    assert exporter.export_sessions(db,session_id='missing',max_sessions=1)==[]


def test_literal_title_prefix_escapes_sql_wildcards_and_case(tmp_path):
    db=db_fixture(tmp_path)
    assert [r['session_id'] for r in exporter.export_sessions(db,title_prefix='Trace%',max_sessions=1)] == ['ses_1']
    assert [r['session_id'] for r in exporter.export_sessions(db,title_prefix='Trace_',max_sessions=1)] == ['ses_2']
    assert [r['session_id'] for r in exporter.export_sessions(db,title_prefix='trace%',max_sessions=1)] == ['ses_3']


def test_fail_closed_before_bulk_session_read(tmp_path):
    db=db_fixture(tmp_path)
    with pytest.raises(ValueError,match='scope exceeds'):exporter.export_sessions(db,max_sessions=2)
    with pytest.raises(ValueError,match='between 1 and 5000'):exporter.export_sessions(db,max_sessions=0)
    assert len(exporter.export_sessions(db,max_sessions=4))==4


def test_readonly_private_atomic_cli_and_missing_session_preserves_evidence(tmp_path,monkeypatch):
    db=db_fixture(tmp_path);out=tmp_path/'private-trace.jsonl'
    assert cli(monkeypatch,db,out,'--session-id','ses_1')==0
    old=out.read_bytes()
    assert len(old.splitlines())==1
    assert (out.stat().st_mode & 0o777)==0o600
    assert cli(monkeypatch,db,out,'--session-id','unavailable')==1
    assert out.read_bytes()==old
    assert cli(monkeypatch,db,out,'--max-sessions','2')==1
    assert out.read_bytes()==old


def test_symlink_output_denied_without_touching_target(tmp_path,monkeypatch):
    db=db_fixture(tmp_path);dest=tmp_path/'other-file';dest.write_text('KEEP')
    link=tmp_path/'export.jsonl';link.symlink_to(dest)
    assert cli(monkeypatch,db,link,'--session-id','ses_1')==1
    assert dest.read_text()=='KEEP'
    assert link.is_symlink()


def test_per_session_message_and_part_caps(tmp_path,monkeypatch):
    db=db_fixture(tmp_path)
    monkeypatch.setattr(exporter,'MAX_EVENTS_PER_SESSION',1)
    con=sqlite3.connect(db)
    for i in range(2):
        con.execute('INSERT INTO message VALUES(?,?,?,?,?)',
                    (f'm{i}','ses_1',i,i,json.dumps({'role':'assistant'})))
    con.commit();con.close()
    with pytest.raises(ValueError,match='message event limit'):exporter.export_sessions(db,session_id='ses_1')
    con=sqlite3.connect(db);con.execute('DELETE FROM message')
    for i in range(2):
        con.execute('INSERT INTO part VALUES(?,?,?,?,?,?)',
                    (f'p{i}',f'm{i}','ses_1',i,i,json.dumps({'type':'tool','tool':'read'})))
    con.commit();con.close()
    with pytest.raises(ValueError,match='part event limit'):exporter.export_sessions(db,session_id='ses_1')


def test_fails_closed_without_mutating_last_receipt_on_nonfinite_json(tmp_path,monkeypatch):
    db=db_fixture(tmp_path);out=tmp_path/'trace.jsonl'
    assert cli(monkeypatch,db,out,'--session-id','ses_1')==0
    prior=out.read_bytes()
    monkeypatch.setattr(exporter,'export_sessions',lambda *args,**kwargs:[{'cost':float('nan')}])
    assert cli(monkeypatch,db,out,'--session-id','ses_1')==1
    assert out.read_bytes()==prior
