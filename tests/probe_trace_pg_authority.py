"""Opt-in, isolated PostgreSQL 17 shared-authority multiprocess admission probe.

No production DSN or API usage. Uses only a named, inspected, internal Docker
fixture and ephemeral UUID epoch, keys and reservations. This is SINGLE primary,
not quorum HA. Worker credentials are fixture superuser trust: NOT production
security isolation. The signer is separate from worker processes but not a
deployed independent custody service.
"""
import json
import multiprocessing as mp
import os
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_pg_authority_research import (
    PostgresTraceAuthority, bootstrap_once, checked_disposable_dsn, _open,
)
from assistx.trace_durable_ledger_research import (
    _canonical, CLOSED, RECEIPT_VERSION,
)

def _contender(epoch, public, ref, begin, start_physical, events):
    import psycopg
    begin.wait(15)
    try:
        authority=PostgresTraceAuthority(epoch,public)
        r=authority.acquire(ref)
        events.put({"ref":ref,"accepted":bool(r.token),"token":r.token,"reason":r.reason})
        if not r.token:
            return
        start_physical.wait(15)
        # It is a real server-side read query, not a mocked outstanding count.
        with psycopg.connect(authority.dsn, connect_timeout=3,
                             application_name="assistx_disposable_authority_probe") as conn:
            with conn.transaction():
                conn.execute("SELECT pg_sleep(2.5)")
    except Exception as exc:
        events.put({"ref":ref,"error":type(exc).__name__})


def _signed_release(authority,key,token,ref,tx_id):
    payload={
        "version":RECEIPT_VERSION,"epoch":authority.epoch,
        "token":token,"query_ref":ref,"evidence_id":tx_id,
        "verdict":CLOSED,
    }
    sig=key.sign(_canonical(payload))
    return authority.release_witnessed(payload,sig),payload,sig


def run():
    if os.getenv("ASSISTX_TRACE_PG_DISPOSABLE_RESEARCH")!="1":
        raise RuntimeError("EXPLICIT_RESEARCH_OPT_IN_REQUIRED")
    uri=checked_disposable_dsn()
    epoch=str(uuid.uuid4())
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    bootstrap_once(uri,epoch,3)
    authority=PostgresTraceAuthority(epoch,public)

    # A runtime process cannot bootstrap a missing/reinitialized authority.
    try:
        bootstrap_once(uri,epoch,3)
        raise AssertionError("GENESIS_REARM_NOT_BLOCKED")
    except RuntimeError as exc:
        assert str(exc)=="REFUSE_TO_REARM_EXISTING_AUTHORITY"
    try:
        PostgresTraceAuthority(str(uuid.uuid4()),public)
        raise AssertionError("WRONG_EPOCH_OPENED")
    except RuntimeError:
        pass

    ctx=mp.get_context("spawn")
    report={"schema":"assistx-pg-shared-authority-research-v1",
            "db":"postgres:17-alpine isolated internal no published ports",
            "authority":"single-primary, not quorum",
            "capacity":3,"state_rebootstrap_denied":True,
            "wrong_external_epoch_denied":True,
            "production_access":False,"workers_have_superuser_trust_credentials":True,
            "runs":[]}
    for count in (1,3,5,10):
        begin,start_physical=ctx.Event(),ctx.Event()
        queue=ctx.Queue()
        tasks=[ctx.Process(target=_contender,args=(epoch,public,f"pg-{count}-{n}",
                  begin,start_physical,queue)) for n in range(count)]
        for p in tasks:p.start()
        begin.set()
        decisions=[queue.get(timeout=25) for _ in tasks]
        assert not [d for d in decisions if d.get("error")],decisions
        admitted=[d for d in decisions if d["accepted"]]
        denied=[d for d in decisions if not d["accepted"]]
        assert len(admitted)==min(count,3),decisions
        assert authority.inspect()==len(admitted)
        start_physical.set()
        active=0
        for _ in range(40):
            with _open(uri) as db:
                active=db.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name='assistx_disposable_authority_probe' "
                    "AND state='active' AND query LIKE 'SELECT pg_sleep%'"
                ).fetchone()[0]
            if active==len(admitted):break
            time.sleep(0.08)
        assert active==len(admitted),("SERVER_QUERY_COUNT",count,active,len(admitted))
        # Refuse release while server-side query is still observed. In this
        # research harness that policy is enforced by independent inspection,
        # NOT cryptographically by the DB or receipt signature.
        if count>=3:
            duplicate=authority.acquire(f"extra-pg-{count}")
            assert duplicate.token is None and duplicate.reason=="full"
        # Observers now wait for every physical session to terminate.
        for p in tasks:
            p.join(9)
            if p.is_alive():
                p.kill();p.join(3)
            assert p.exitcode==0, (count,p.exitcode)
        with _open(uri) as db:
            active_after=db.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE application_name='assistx_disposable_authority_probe' "
                "AND state='active' AND query LIKE 'SELECT pg_sleep%'"
            ).fetchone()[0]
        assert active_after==0
        assert authority.inspect()==len(admitted)
        forged=key.sign(b"not-a-valid-receipt")
        # Each slot requires its exact signed receipt, not worker exit.
        for entry in admitted:
            bad_payload={"version":RECEIPT_VERSION,"epoch":epoch,
                        "token":entry["token"],"query_ref":entry["ref"],
                        "evidence_id":f"pgsynthetic{count}", "verdict":CLOSED}
            assert not authority.release_witnessed(bad_payload,forged)
            assert authority.acquire(entry["ref"]).token is None
            ok,body,sig=_signed_release(
                authority,key,entry["token"],entry["ref"],f"pgoverview{count}")
            assert ok
            assert not authority.release_witnessed(body,sig)
        assert authority.inspect()==0
        report["runs"].append({"attempts":count,"accepted":len(admitted),
            "denied":len(denied),"physically_observed_server_reads":active,
            "slots_after_worker_exit_before_witness":len(admitted),
            "slots_after_exact_signed_releases":0})
    print(json.dumps(report,sort_keys=True,indent=2))
    return report


if __name__=="__main__":
    run()
