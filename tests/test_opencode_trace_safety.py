"""Focused read-only, truthful-custody and privacy regression contracts."""
import importlib.util
import json
import pathlib
import sqlite3

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sprint_trace_exporter', ROOT / 'scripts/export_opencode_session_traces.py')
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def fixture(tmp_path, *, sid='ses_same', title='safe-title', model='model:free', cost=None):
    db = tmp_path / 'trace.db'
    con = sqlite3.connect(db)
    con.executescript('''
        CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT,
         model TEXT, cost REAL, tokens_input INTEGER, tokens_output INTEGER,
         tokens_reasoning INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER,
         time_created INTEGER, time_updated INTEGER);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
    ''')
    con.execute('INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (sid,None,str(tmp_path),title,json.dumps({'providerID':'kilo_free','id':model}),
         cost,10,3,0,0,0,1791543706569,1791543706570))
    con.execute('INSERT INTO message VALUES (?,?,?,?,?)',
        ('msg',sid,1791543706570,1791543706570,json.dumps({'role':'assistant','error':{'message':'sk_TESTSECRETMATERIAL00000000000000000000'}})))
    con.commit();con.close();return db


def test_same_identifier_across_independent_snapshots_is_not_dropped(tmp_path):
    left=tmp_path/'left';left.mkdir();right=tmp_path/'right';right.mkdir()
    a=exporter.export_sessions(fixture(left))
    b=exporter.export_sessions(fixture(right))
    assert len(a)==len(b)==1
    assert a[0]['session_id']==b[0]['session_id']=='ses_same'


def test_unverified_cost_remains_unknown_and_errors_redacted(tmp_path):
    record=exporter.export_sessions(fixture(tmp_path))[0]
    assert record['cost'] is None
    assert record['cost_breakdown']['input_cost'] is None
    assert record['efficiency_metrics']['cost_per_1k_input_tokens'] is None
    assert record['retry_count'] is None and record['max_retries'] is None
    assert record['error_summary']['assistant_errors']==['[REDACTED]']
    assert 'sk_TESTSECRET' not in json.dumps(record)


def test_explicit_session_id_limits_read_only_scope(tmp_path):
    db=fixture(tmp_path)
    assert len(exporter.export_sessions(db, session_id='ses_same', max_sessions=1)) == 1
    assert exporter.export_sessions(db, session_id='not-present', max_sessions=1) == []
    assert exporter.export_sessions(db, title_prefix='safe-', max_sessions=1)[0]['session_id']=='ses_same'
    assert exporter.export_sessions(db, title_prefix='other', max_sessions=1)==[]


def test_bounded_scope_rejects_unqualified_bulk_queries(tmp_path):
    db=fixture(tmp_path)
    con=sqlite3.connect(db)
    con.execute('INSERT INTO session SELECT ?, parent_id, directory, ?, model, cost, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, time_created, time_updated FROM session',('ses_other','another'))
    con.commit();con.close()
    with pytest.raises(ValueError,match='scope exceeds'):exporter.export_sessions(db,max_sessions=1)
    with pytest.raises(ValueError,match='between 1 and 5000'):exporter.export_sessions(db,max_sessions=0)


def test_untrusted_model_name_does_not_invent_paid_cost(tmp_path):
    record=exporter.export_sessions(fixture(tmp_path,model='fictional/unknown:free',cost=12.0))[0]
    assert record['cost']==12.0
    assert record['cost_breakdown']['input_cost'] is None
    assert record['cost_breakdown']['output_cost'] is None
    # Classification is model metadata only, not verified provider billing.
    assert record['free_model_type'] in {'opencode-native-free','kilo-anonymous'}


def test_sensitive_field_and_credential_strings_are_redacted():
    data={'api_key':'do-not-persist','nested':{'authorization':'Bearer token'}, 'title':'safe ghp_abcdefghijklmnopqrstuvwxyz123456789'}
    result=exporter._redact_sensitive_data(data)
    assert result['api_key']=='[REDACTED]'
    assert result['nested']['authorization']=='[REDACTED]'
    assert 'ghp_abc' not in result['title']


def test_openrouter_unpriced_suffix_does_not_prove_free_billing(tmp_path):
    db=fixture(tmp_path,model='openrouter/example:free',cost=0.0)
    con=sqlite3.connect(db)
    con.execute('UPDATE session SET model = ? WHERE id = ?',
        (json.dumps({'providerID':'openrouter','id':'openrouter/example:free'}),'ses_same'))
    con.commit();con.close()
    record=exporter.export_sessions(db)[0]
    assert record['free_model_type']=='free-suffix-unverified'
    assert record['is_free_provider'] is False
    assert record['provider_billing_verified'] is False


def test_nested_reserved_keys_are_denied():
    assert not exporter._validate_jsonl_safety({'nested':{'__proto__':{'bad':'value'}}})
    assert not exporter._validate_jsonl_safety({'nested':[{'constructor': 'bad'}]})
    assert exporter._validate_jsonl_safety({'nested':{'allowed':'okay'}})


def test_invalid_cost_reports_sanitized_failure_without_discarding_other_db(tmp_path):
    (tmp_path/'broken').mkdir();db=fixture(tmp_path/'broken')
    con=sqlite3.connect(db)
    con.execute('UPDATE session SET cost = ? WHERE id = ?',('not-a-number','ses_same'))
    con.commit();con.close()
    assert exporter.export_sessions(db)==[]
    assert exporter._export_stats['errors_encountered']>=1
    assert exporter._export_stats['failed_session_ids']==['ses_same']
    (tmp_path/'valid').mkdir()
    assert len(exporter.export_sessions(fixture(tmp_path/'valid')))==1
    assert exporter._export_stats['failed_session_ids']==[]


def test_cli_writes_private_atomic_jsonl_and_denies_missing_id(tmp_path,monkeypatch,capsys):
    import sys
    db=fixture(tmp_path)
    out=tmp_path/'trace.jsonl'
    monkeypatch.setattr(sys,'argv',['exporter','--db',str(db),'--out',str(out),
                                    '--session-id','ses_same','--max-sessions','1'])
    assert exporter.main()==0
    assert out.stat().st_mode & 0o777 == 0o600
    assert len(out.read_text().splitlines())==1
    monkeypatch.setattr(sys,'argv',['exporter','--db',str(db),'--out',str(out),
                                    '--session-id','no-such-session'])
    assert exporter.main()==1
    assert len(out.read_text().splitlines())==1  # Prior successful receipt intact
