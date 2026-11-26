#!/usr/bin/env python3
"""Rehearse candidate against empty, SQLite-consistent copies in existing runtimes.

No live migration, producer stop, restart, credential readout, event submission,
or live payload export. Nonempty or unavailable live database => no rehearsal.
"""
import base64,hashlib,json,pathlib,subprocess,sys
HOSTS=('assistx-api','assistx-worker','assistx-hermes-adapter')
OLD='e5fe954e17458a3ebf1b507e2c7b3c833ca6beaec94b20889ac4dcbd9d9203ec'
NEW='8bdd8637a1793a931a6e47665fab5d78c59bcca4001945f29c29d8b046d41345'
ROOT=pathlib.Path(__file__).resolve().parents[1]
REMOTE=r"""
import base64,hashlib,importlib.util,json,os,pathlib,sqlite3,sys,tempfile,types
old,new=sys.argv[1:]
source=base64.b64decode(sys.stdin.buffer.read(131073),validate=True)
assert len(source)<=65536 and hashlib.sha256(source).hexdigest()==new
spec=importlib.util.find_spec('assistx.outbox_client')
assert spec and hashlib.sha256(pathlib.Path(spec.origin).read_bytes()).hexdigest()==old
p=pathlib.Path(os.path.expanduser(os.getenv('ASSISTX_OUTBOX_DB','~/.assistx_outbox.db')))
assert p.is_file()
uri=p.resolve().as_uri()+'?mode=ro'
with tempfile.TemporaryDirectory(prefix='assistx-outbox-rehearsal-') as t:
    target=pathlib.Path(t)/'fixture.sqlite'
    with sqlite3.connect(uri,uri=True,timeout=.5) as live:
        live.execute('PRAGMA query_only=ON')
        live.execute('BEGIN')
        cols=[row[1] for row in live.execute('PRAGMA table_info(outbox)')]
        assert cols==['outbox_id','event_id','payload_json','attempt_count','last_attempt_at','status','created_at']
        assert live.execute('SELECT 1 FROM outbox LIMIT 1').fetchone() is None, 'nonempty-live-db'
        with sqlite3.connect(target) as copy:
            live.backup(copy,pages=16)
        live.rollback()
    with sqlite3.connect(target) as copy:
        assert copy.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert copy.execute('SELECT COUNT(*) FROM outbox').fetchone()==(0,)
        copy.execute("INSERT INTO outbox VALUES ('synthetic-legacy','synthetic-event','{}',0,NULL,'pending','2026-11-25T00:00:00+00:00')")
    m=types.ModuleType('candidate_outbox')
    exec(compile(source,'candidate_outbox.py','exec'),m.__dict__)
    c=m.OutboxClient(db_path=str(target),api_url='',api_user='',api_pass='',auto_flush=False)
    with sqlite3.connect(target) as copy:
        migrated=[row[1] for row in copy.execute('PRAGMA table_info(outbox)')]
        assert migrated==cols+['claim_owner','claimed_at']
        assert copy.execute('SELECT COUNT(*) FROM outbox').fetchone()==(1,)
        assert copy.execute('PRAGMA integrity_check').fetchone()==('ok',)
    claim=c._claim_next()
    assert claim.outbox_id=='synthetic-legacy' and claim.claim_owner
    assert c._release(claim,'delivered',1)
    assert not c._release(claim,'pending',0)
    assert c.get_stats()['delivered']==1
    a=c.enqueue({'event_id':'synthetic-dedup'})
    b=c.enqueue({'event_id':'synthetic-dedup'})
    assert a.outbox_id==b.outbox_id
    assert c.flush()==0
    assert c.get_stats()['total']==2
    # Prove the live DB is still legacy; a concurrent legitimate producer
    # could add a row, so report presence only, not a guarantee of emptiness.
    with sqlite3.connect(uri,uri=True,timeout=.5) as live:
        assert [r[1] for r in live.execute('PRAGMA table_info(outbox)')]==cols
        final_empty=live.execute('SELECT 1 FROM outbox LIMIT 1').fetchone() is None
print(json.dumps({'schema':'assistx-outbox-runtime-rehearsal-v1','liveSourceSha256':old,'candidateSha256':new,'liveSchemaUnchanged':True,'liveEmptyAtSnapshot':True,'liveEmptyAtEnd':final_empty,'snapshotIntegrityVerified':True,'migratedCopyIntegrityVerified':True,'syntheticLegacyRowPreserved':True,'claimReleaseVerified':True,'staleReleaseRejected':True,'dedupVerified':True,'unconfiguredDeliverySuppressed':True,'networkRequests':0,'liveDatabaseWrites':0},sort_keys=True))
"""
def main():
    raw=(ROOT/'src/assistx/outbox_client.py').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=NEW:raise SystemExit('candidate source pin mismatch')
    results={}
    for name in HOSTS:
        try:
            p=subprocess.run(['docker','exec','-i',name,'python','-B','-c',REMOTE,OLD,NEW],input=base64.b64encode(raw),capture_output=True,timeout=12,check=True)
            row=json.loads(p.stdout)
            if row.get('schema')!='assistx-outbox-runtime-rehearsal-v1' or row.get('candidateSha256')!=NEW or row.get('liveSourceSha256')!=OLD or row.get('liveDatabaseWrites')!=0:raise ValueError('receipt')
            results[name]=row
        except (OSError,ValueError,subprocess.SubprocessError) as e:
            results[name]={'state':'not-verified','reason':type(e).__name__}
    ok=all(x.get('schema')=='assistx-outbox-runtime-rehearsal-v1' for x in results.values())
    print(json.dumps({'state':'rehearsed' if ok else 'not-verified','containers':results,'liveCutoverPerformed':False},sort_keys=True))
    return 0 if ok else 2
if __name__=='__main__':sys.exit(main())
