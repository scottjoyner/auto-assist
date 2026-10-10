"""Opt-in physical split-primary COUNTEREXAMPLE with PostgreSQL 17.

Two independent disposable single-writer PG instances deliberately share a
research epoch. Each server accepts cap=1, so treating either as global
authority permits TWO reservations. A separate *single local* term controller
prevents takeover while its attempt is unresolved, but is NOT quorum safe.
No production DBs, published ports, host binds or graph queries.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from probe_trace_pg_privileged_roles import (
    _dsn, bootstrap_once, WORKER, VERIFIER
)
from trace_fencing_term_authority_research import (
    FenceDenied, ResearchFencingAuthority, _canon,
    make_takeover_request
)


def docker(*args, check=True, timeout=18):
    try:
        return subprocess.run(["docker", *args], capture_output=True,
                              text=True, timeout=timeout, check=check)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Never stringify Docker command when environment contains passwords.
        raise RuntimeError("DISPOSABLE_DOCKER_COMMAND_FAILED") from None


def read_ip(name, network):
    row = json.loads(docker("inspect", name).stdout)[0]
    cfg, host = row["Config"], row["HostConfig"]
    if (row["Name"] != "/" + name or cfg["Image"] != "postgres:17-alpine"
            or host["NetworkMode"] != network
            or set(row["NetworkSettings"]["Networks"]) != {network}
            or host.get("PortBindings") or host.get("Binds")
            or any(m["Type"] == "bind" for m in row.get("Mounts", []))):
        raise RuntimeError("UNSAFE_PG_RESEARCH_FIXTURE")
    return row["NetworkSettings"]["Networks"][network]["IPAddress"]


def start_and_bootstrap(name, network, admin_pw, worker_pw,
                        verifier_pw, epoch):
    docker("run", "-d", "--name", name, "--pull", "never",
           "--network", network, "--memory", "512m", "--cpus", "1",
           "-e", "POSTGRES_PASSWORD=" + admin_pw,
           "-e", "POSTGRES_HOST_AUTH_METHOD=scram-sha-256",
           "postgres:17-alpine", timeout=25)
    ip = read_ip(name, network)
    admin_dsn = _dsn("postgres", admin_pw, ip)
    for _ in range(40):
        try:
            with psycopg.connect(admin_dsn, connect_timeout=2):
                break
        except psycopg.Error:
            time.sleep(0.5)
    else:
        raise RuntimeError("PG_FIXTURE_NOT_READY")
    bootstrap_once(admin_dsn, worker_pw, verifier_pw, epoch)
    with psycopg.connect(admin_dsn, connect_timeout=3) as db:
        db.execute(
            "UPDATE assistx_trace_fence_research.authority SET capacity=1"
        )
    return _dsn(WORKER, worker_pw, ip)


def physical_admit(dsn, epoch, token, ref):
    with psycopg.connect(dsn, connect_timeout=3) as db:
        result = db.execute(
            "SELECT * FROM assistx_trace_fence_research.admit(%s,%s,%s)",
            (epoch, token, ref)
        ).fetchone()
        db.commit()
        return result


def run():
    if os.environ.get("ASSISTX_TWO_PRIMARY_FENCE_RESEARCH") != "1":
        raise RuntimeError("EXPLICIT_TWO_PRIMARY_OPT_IN_REQUIRED")
    run_id = secrets.token_hex(4)
    network = "assistx-fence-two-pg-net-" + run_id
    first = "assistx-fence-pg-a-" + run_id
    second = "assistx-fence-pg-b-" + run_id
    epoch = str(uuid4())
    admin_pw = secrets.token_hex(22)
    worker_pw = secrets.token_hex(22)
    verifier_pw = secrets.token_hex(22)
    containers, network_created = [], False
    try:
        docker("network", "create", "--internal", "--driver", "bridge",
               "--label", "assistx.trace.research=two-primary-fence",
               network)
        network_created = True
        for name in (first, second):
            # Append BEFORE initialization so even partial failures clean up.
            containers.append(name)
            dsn = start_and_bootstrap(name, network, admin_pw, worker_pw,
                                      verifier_pw, epoch)
            if name == first:
                dsn_a = dsn
            else:
                dsn_b = dsn
        accepted_a = physical_admit(
            dsn_a, epoch, secrets.token_hex(16), "independent-primary-A"
        )
        accepted_b = physical_admit(
            dsn_b, epoch, secrets.token_hex(16), "independent-primary-B"
        )
        assert accepted_a[0] is True and accepted_b[0] is True, (
            "COUNTEREXAMPLE_WAS_NOT_REPRODUCED"
        )

        with tempfile.TemporaryDirectory(prefix="assistx-fence-term-") as temp:
            root = Path(temp)
            witness = Ed25519PrivateKey.generate()
            operator = Ed25519PrivateKey.generate()
            pub = lambda key: key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            authority = ResearchFencingAuthority.bootstrap(
                root / "term-authority.sqlite",
                root / "checkpoint.json", "gateway-A", 1,
                pub(witness), pub(operator)
            )
            admitted = authority.admit(
                "gateway-A", 1, "gateway-A-approved-operation"
            )
            assert admitted and authority.snapshot()["pending"] == 1
            try:
                authority.admit(
                    "gateway-A", 1, "gateway-B-oversubscribed"
                )
            except FenceDenied as exc:
                assert str(exc) == "PENDING_PHYSICAL_CAPACITY"
            else:
                raise AssertionError("SEPARATE_CONTROLLER_OVERADMITTED")
            proposed = make_takeover_request(
                authority.snapshot(), "gateway-B"
            )
            signature = operator.sign(_canon(proposed))
            try:
                authority.takeover("gateway-B", 1, proposed, signature)
            except FenceDenied as exc:
                assert str(exc) == "UNCERTAIN_INFLIGHT_TAKEOVER_BLOCKED"
            else:
                raise AssertionError("UNCERTAIN_TAKEOVER_SUCCEEDED")
            authority.quarantine(admitted)
            # A local authority restart must not clear physical uncertainty.
            restarted = ResearchFencingAuthority(
                root / "term-authority.sqlite", root / "checkpoint.json"
            )
            assert restarted.snapshot()["pending"] == 1
            try:
                restarted.admit("gateway-B", 2, "future-owner")
            except FenceDenied as exc:
                assert str(exc) == "STALE_FENCING_TERM"
            else:
                raise AssertionError("UNAUTHORIZED_TERM_ACCEPTED")
        return {
            "schema": "assistx-two-independent-pg17-term-negative-v1",
            "two_disposable_primaries_with_shared_epoch": True,
            "pg_primary_a_admitted": True,
            "pg_primary_b_admitted": True,
            "dual_pg_cap_one_overadmission_reproduced": True,
            "single_local_controller_denied_second": True,
            "operator_signed_takeover_blocked_by_pending_work": True,
            "authority_restart_retained_uncertainty": True,
            "production_authority": False,
            "copied_pg_disk_state_tested": False,
            "quorum_failover_proven": False,
            "neo4j_stale_effects_fenced": False,
        }
    finally:
        for name in reversed(containers):
            docker("rm", "-f", "-v", name, check=False, timeout=20)
        if network_created:
            docker("network", "rm", network, check=False, timeout=20)


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
