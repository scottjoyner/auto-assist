"""RESEARCH ONLY: detached Neo4j transaction observer owns PG verifier privilege.

Single-host experiment with two *separate* named internal Docker fixtures:
Neo4j 5.26 and PostgreSQL 17 SCRAM; no production mounts or ports. The
worker never receives the verifier password or Ed25519 signing key.
No runtime API imports this file. Neo4j query comments remain worker-supplied,
not server-enforced query/token identity; cluster/quorum safety unproven.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from neo4j import GraphDatabase

from probe_trace_pg_privileged_roles import (
    _address, _dsn, _open, bootstrap_once, WORKER, VERIFIER,
)
from probe_trace_physical_lifecycle_526 import checked_uri, _transactions, MARKER
from probe_trace_transport_blackhole_526 import _Proxy
import threading


def _observed_exact(rows, token):
    """Reject missing or ambiguous server-side candidate, even if ref known."""
    marker = MARKER + "_" + token
    found = [
        r for r in rows if r.get("database") == "neo4j"
        and str(r.get("currentQuery") or "").startswith("UNWIND")
        and marker in (r.get("currentQuery") or "")
        and re.fullmatch(r"neo4j-transaction-[0-9]+", str(r.get("transactionId") or ""))
    ]
    return found[0]["transactionId"] if len(found) == 1 else None


def _read_worker(pg_ip, worker_password, neo_uri, epoch, query_ref, channel):
    """Only worker-authenticated PG admit/inspect and Neo4j READ."""
    from neo4j import GraphDatabase
    token = uuid.uuid4().hex
    try:
        with _open(_dsn(WORKER, worker_password, pg_ip)) as db:
            admitted, _, reason = db.execute(
                "SELECT * FROM assistx_trace_fence_research.admit(%s,%s,%s)",
                (epoch,token,query_ref)).fetchone()
            db.commit()
        if not admitted:
            channel.send({"admitted":False,"reason":reason})
            return
        channel.send({"admitted":True,"token":token})
        query = (
            "UNWIND range(1, 110000) AS n UNWIND range(1, 110000) AS m "
            "WITH n,m WHERE (n*m)%97=7 RETURN count(*) AS count /* "
            + MARKER + "_" + token + " */"
        )
        with GraphDatabase.driver(
            neo_uri, auth=None, connection_timeout=3,
            connection_acquisition_timeout=4,
        ) as graph:
            with graph.session(database="neo4j") as session:
                with session.begin_transaction(timeout=30) as tx:
                    channel.send({"read_started":True})
                    tx.run(query).consume()
    except Exception as exc:
        try: channel.send({"error":type(exc).__name__})
        except Exception: pass


def _observer(pg_ip, verifier_password, neo_uri, epoch, conn):
    """Owns verifier-only credential and ephemeral signing key.

    A caller can request checks but cannot mint closure or call verifier PG.
    Signs only after two distinct successful SHOW TRANSACTIONS observations
    show the original previously observed transaction absent.
    """
    signer = Ed25519PrivateKey.generate()
    pub = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    observed = {}
    pg = _dsn(VERIFIER,verifier_password,pg_ip)
    with GraphDatabase.driver(neo_uri, auth=None, connection_timeout=3) as graph:
        conn.send({"pub":pub})
        while True:
            command = conn.recv()
            if command.get("op")=="stop":return
            if command.get("op")=="observe":
                token, ref = command["token"],command["ref"]
                if not re.fullmatch(r"[0-9a-f]{32}",token):
                    conn.send({"result":"invalid-token"})
                    continue
                found = None
                for _ in range(50):
                    found=_observed_exact(_transactions(graph), token)
                    if found:break
                    time.sleep(.12)
                if found:
                    observed[token]=(found,ref,epoch)
                conn.send({"result":"observed" if found else "not-observed",
                           "txid":found})
                continue
            if command.get("op")!="release":
                conn.send({"result":"invalid-op"})
                continue
            token,ref = command["token"],command["ref"]
            binding = observed.get(token)
            if not binding or binding[1:]!=(ref,epoch):
                conn.send({"result":"unobserved-or-mismatch"})
                continue
            txid=binding[0]
            absent=0
            try:
                for _ in range(32):
                    # If Neo4j cannot be independently reached, NEVER free.
                    rows=_transactions(graph)
                    absent=absent+1 if not any(
                        r["transactionId"]==txid for r in rows) else 0
                    if absent>=2:break
                    time.sleep(.12)
            except Exception:
                conn.send({"result":"neo4j-unavailable"})
                continue
            if absent<2:
                conn.send({"result":"still-visible"})
                continue
            with _open(pg) as db:
                deleted=db.execute(
                    "SELECT assistx_trace_fence_research.release_exact(%s,%s,%s)",
                    (epoch,token,ref)).fetchone()[0]
                db.commit()
            if deleted is not True:
                conn.send({"result":"no-exact-slot"})
                continue
            evidence={
                "schema":"assistx-pg-neo4j-research-receipt-v1",
                "transaction_id":txid,"token":token,
                "epoch":epoch,"query_ref":ref,
                "observation":"two-distinct-absent-snapshots",
                "role":"separate-pg-verifier-process",
            }
            signature=signer.sign(
                json.dumps(evidence,sort_keys=True,separators=(",",":")).encode()
            ).hex()
            del observed[token]
            conn.send({"result":"released","receipt":evidence,
                       "signature":signature})


def _capacity(pg_ip,passcode,epoch):
    with _open(_dsn(WORKER,passcode,pg_ip)) as db:
        return db.execute(
            "SELECT assistx_trace_fence_research.inspect(%s)",(epoch,)
        ).fetchone()[0]


def run():
    if os.getenv("ASSISTX_TRACE_PG_NEO4J_WITNESS_RESEARCH")!="1":
        raise RuntimeError("EXPLICIT_WITNESS_PROBE_OPT_IN_REQUIRED")
    pg_ip=_address()   # independently checks exact isolated named PG fixture
    neo_uri=checked_uri()  # independently checks exact isolated Neo4j fixture
    admin_pw=os.environ["ASSISTX_TRACE_PG_ADMIN_PASSWORD"]
    worker_pw=os.environ["ASSISTX_TRACE_PG_WORKER_PASSWORD"]
    verifier_pw=os.environ["ASSISTX_TRACE_PG_VERIFIER_PASSWORD"]
    epoch=str(uuid.uuid4())
    admin=_dsn("postgres",admin_pw,pg_ip)
    bootstrap_once(admin,worker_pw,verifier_pw,epoch)
    # This specific research experiment is capacity ONE. No runtime policy
    # can arbitrarily adjust capacity (admin/bootstrap only).
    with _open(admin) as db:
        db.execute("UPDATE assistx_trace_fence_research.authority SET capacity=1")
    ctx=mp.get_context("spawn")
    proxy=_Proxy(("127.0.0.1",0),neo_uri.removeprefix("bolt://").split(":")[0])
    server=threading.Thread(target=proxy.serve_forever,daemon=True)
    server.start()
    witness=None
    worker=None
    try:
        up, down=ctx.Pipe()
        witness=ctx.Process(target=_observer,args=(pg_ip,verifier_pw,neo_uri,epoch,down))
        witness.start()
        assert up.poll(12)
        public=up.recv()["pub"]
        read,write=ctx.Pipe(duplex=False)
        worker=ctx.Process(target=_read_worker,args=(
            pg_ip,worker_pw,f"bolt://127.0.0.1:{proxy.server_address[1]}",
            epoch,"physical-graph-one",write))
        worker.start()
        assert read.poll(12)
        first=read.recv()
        assert first.get("admitted") is True, first
        token=first["token"]
        assert read.poll(12) and read.recv().get("read_started") is True
        up.send({"op":"observe","token":token,"ref":"physical-graph-one"})
        assert up.poll(12)
        sighting=up.recv()
        assert sighting["result"]=="observed", sighting
        txid=sighting["txid"]
        assert _capacity(pg_ip,worker_pw,epoch)==1

        # An observer must not release another token or an ACTIVE query.
        up.send({"op":"release","token":"0"*32,"ref":"physical-graph-one"})
        assert up.poll(8) and up.recv()["result"]=="unobserved-or-mismatch"
        up.send({"op":"release","token":token,"ref":"physical-graph-one"})
        assert up.poll(12) and up.recv()["result"]=="still-visible"
        assert _capacity(pg_ip,worker_pw,epoch)==1

        proxy.blackout.set()
        time.sleep(.15)
        worker.kill()
        worker.join(4)
        assert worker.exitcode==-9
        with GraphDatabase.driver(neo_uri,auth=None,connection_timeout=3) as graph:
            assert any(r["transactionId"]==txid for r in _transactions(graph))
        # Empty capacity is still denied across the crash/blackholed remote.
        with _open(_dsn(WORKER,worker_pw,pg_ip)) as db:
            check=db.execute(
                "SELECT * FROM assistx_trace_fence_research.admit(%s,%s,%s)",
                (epoch,uuid.uuid4().hex,"premature-successor")).fetchone()
        assert check[0] is False and check[2]=="full"
        up.send({"op":"release","token":token,"ref":"physical-graph-one"})
        assert up.poll(12) and up.recv()["result"]=="still-visible"
        assert _capacity(pg_ip,worker_pw,epoch)==1

        # Explicitly end held transport. Neither the worker nor coordinator
        # possesses the verifier-only PG credential / signing key in this probe.
        proxy.quit.set()
        proxy.blackout.clear()
        up.send({"op":"release","token":token,"ref":"physical-graph-one"})
        assert up.poll(12)
        proof=up.recv()
        assert proof["result"]=="released",proof
        assert proof["receipt"]["transaction_id"]==txid
        # Independently check the detached signer response: no private
        # material leaves the verifier process, and neither the worker nor
        # coordinator can forge another signed closure.
        canonical=json.dumps(
            proof["receipt"],sort_keys=True,separators=(",",":")
        ).encode()
        verifier=Ed25519PublicKey.from_public_bytes(bytes.fromhex(public))
        verifier.verify(bytes.fromhex(proof["signature"]),canonical)
        tampered=dict(proof["receipt"],token="f"*32)
        from cryptography.exceptions import InvalidSignature
        try:
            verifier.verify(
                bytes.fromhex(proof["signature"]),
                json.dumps(tampered,sort_keys=True,separators=(",",":")).encode()
            )
        except InvalidSignature:
            pass
        else:
            raise AssertionError("TAMPERED_CLOSURE_SIGNATURE_ACCEPTED")
        assert _capacity(pg_ip,worker_pw,epoch)==0
        up.send({"op":"release","token":token,"ref":"physical-graph-one"})
        assert up.poll(8) and up.recv()["result"]=="unobserved-or-mismatch"
        up.send({"op":"stop"})
        witness.join(5)
        assert witness.exitcode==0
        report={
            "schema":"assistx-pg-neo4j-physical-custody-v1",
            "source":"exact-named-disposable-internal-postgres17-and-neo4j526",
            "server_transaction_id":txid,
            "worker_sigkill":-9,
            "blackholed_transport_kept_neo4j_running":True,
            "pg_capacity_held_during_query_and_after_worker_death":True,
            "wrong_token_denied":True,
            "active_transaction_release_denied":True,
            "independent_observer_released_after_physical_absence":True,
            "signed_witness_evidence_exists":True,
            "detached_signature_verified_independently":True,
            "tampered_signature_rejected":True,
            "witness_public_key_prefix":public[:16],
            "verifier_password_never_passed_to_worker":True,
            "production_authority":False,
            "neo4j_server_enforced_token_binding":False,
            "global_quorum_failover_proven":False,
        }
        print(json.dumps(report,sort_keys=True,indent=2))
        return report
    finally:
        if worker and worker.is_alive():worker.kill();worker.join(3)
        if witness and witness.is_alive():witness.kill();witness.join(3)
        proxy.quit.set();proxy.blackout.clear();proxy.shutdown();proxy.server_close()
        server.join(3)


if __name__=="__main__":
    run()
