"""Physical Neo4j role-separation research, opt-in, disposable instance ONLY.

No host ports, no production endpoints/credentials, no persistent mounts.
Proves ordinary API/worker users cannot directly modify canonical projection
or its replay receipts. Also deliberately reproduces a compromised *broker*
credential writing an arbitrary digest: RBAC != cryptographic verification.
"""
from __future__ import annotations

from collections import Counter
import json
import os
import secrets
import subprocess
import time

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

IMAGE = "neo4j:5.26-enterprise"
OPT_IN = "ASSISTX_P0_DISPOSABLE_ROLE_PROOF"
LABEL = "assistx.p0.research=approval-role-split"


def docker(*args: str, timeout: int = 20) -> str:
    result = subprocess.run(
        ["docker", *args], text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # Docker stderr may contain runtime details. Never echo it.
        raise RuntimeError("DOCKER_ACTION_FAILED_" + args[0])
    return result.stdout.strip()


def query(driver, statement: str, *, db: str = "neo4j",
          params: dict | None = None):
    with driver.session(database=db) as session:
        return [dict(x) for x in session.run(statement, params or {}, timeout=5)]


def must_deny(driver, statement: str, *, params: dict | None = None) -> bool:
    try:
        query(driver, statement, params=params)
    except Neo4jError:
        return True
    raise RuntimeError("UNAUTHORIZED_GRAPH_WRITE_SUCCEEDED")


def assert_disposable_container(name: str, network: str) -> str:
    info = json.loads(docker("inspect", name))[0]
    if (set(info["NetworkSettings"]["Networks"]) != {network}
            or info["HostConfig"].get("PortBindings")
            or info["HostConfig"].get("Binds")):
        raise RuntimeError("ISOLATED_GRAPH_TOPOLOGY_UNSAFE")
    for mount in info.get("Mounts", []):
        if (mount.get("Type") != "volume"
                or mount.get("Destination") not in ("/data", "/logs")
                or len(mount.get("Name", "")) < 50):
            raise RuntimeError("NONANONYMOUS_DISPOSABLE_VOLUME")
    ip = info["NetworkSettings"]["Networks"][network].get("IPAddress")
    if not ip:
        raise RuntimeError("DISPOSABLE_GRAPH_IP_MISSING")
    return "bolt://" + ip + ":7687"


def run() -> dict:
    if os.environ.get(OPT_IN) != "1":
        raise RuntimeError("EXPLICIT_DISPOSABLE_OPT_IN_REQUIRED")
    docker("image", "inspect", IMAGE, timeout=10)
    suffix = secrets.token_hex(6)
    network = "assistx-p0-role-only-" + suffix
    container = "assistx-p0-neo-rbac-" + suffix
    admin_password = "X" + secrets.token_urlsafe(32)
    user_passwords = {name: "X" + secrets.token_urlsafe(32)
                      for name in ("fixture_p0_api", "fixture_p0_worker",
                                   "fixture_p0_broker")}
    network_created = False
    container_created = False
    start = time.monotonic()
    try:
        docker("network", "create", "--internal", "--driver", "bridge",
               "--label", LABEL, network)
        network_created = True
        docker("run", "-d", "--pull", "never", "--name", container,
               "--network", network, "--network-alias", "p0-fixture-graph",
               "--memory", "2300m", "--cpus", "1.5", "--pids-limit", "256",
               "--env", "NEO4J_AUTH=neo4j/" + admin_password,
               "--env", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
               "--env", "NEO4J_server_memory_heap_initial__size=256m",
               "--env", "NEO4J_server_memory_heap_max__size=512m",
               IMAGE, timeout=30)
        container_created = True
        uri = assert_disposable_container(container, network)
        errors = Counter()
        with GraphDatabase.driver(
                uri, auth=("neo4j", admin_password),
                connection_timeout=2, connection_acquisition_timeout=3) as admin:
            deadline = time.monotonic() + 75
            while True:
                try:
                    admin.verify_connectivity()
                    if query(admin, "RETURN 1 AS ok") == [{"ok": 1}]:
                        break
                except (ServiceUnavailable, Neo4jError, OSError, RuntimeError) as exc:
                    errors[type(exc).__name__] += 1
                if time.monotonic() > deadline:
                    raise RuntimeError("DISPOSABLE_GRAPH_UNAVAILABLE")
                time.sleep(2)
            for statement in (
                "CREATE CONSTRAINT p0_canonical_name IF NOT EXISTS "
                "FOR (n:FleetProjectionState) REQUIRE n.name IS UNIQUE",
                "CREATE CONSTRAINT p0_approval_id IF NOT EXISTS "
                "FOR (n:RuntimeProjectionApprovalReceipt) "
                "REQUIRE n.approval_id IS UNIQUE",
                "CREATE (:FleetProjectionState {name:'canonical',"
                "generation:643,expires_at_ts:123456,source_receipt_sha256:'old'})",
            ):
                query(admin, statement)
            statements = [
                "CREATE ROLE fixture_p0_api_role IF NOT EXISTS",
                "CREATE ROLE fixture_p0_worker_role IF NOT EXISTS",
                "CREATE ROLE fixture_p0_broker_role IF NOT EXISTS",
            ]
            for role in ("fixture_p0_api_role", "fixture_p0_worker_role",
                         "fixture_p0_broker_role"):
                statements.append(
                    "GRANT ACCESS ON DATABASE neo4j TO " + role)
                statements.append(
                    "GRANT MATCH {name,generation,expires_at_ts,"
                    "source_receipt_sha256} ON GRAPH neo4j "
                    "NODES FleetProjectionState TO " + role)
            statements += [
                "GRANT MATCH {approval_id,generation,source_receipt_sha256} "
                "ON GRAPH neo4j NODES RuntimeProjectionApprovalReceipt "
                "TO fixture_p0_broker_role",
                "GRANT SET PROPERTY {generation,expires_at_ts,"
                "source_receipt_sha256} ON GRAPH neo4j "
                "NODES FleetProjectionState TO fixture_p0_broker_role",
                "GRANT CREATE ON GRAPH neo4j NODES "
                "RuntimeProjectionApprovalReceipt TO fixture_p0_broker_role",
                "GRANT SET PROPERTY {approval_id,generation,"
                "source_receipt_sha256} ON GRAPH neo4j "
                "NODES RuntimeProjectionApprovalReceipt "
                "TO fixture_p0_broker_role",
            ]
            for name, password in user_passwords.items():
                role = name + "_role"
                statements += [
                    "CREATE USER " + name + " SET PASSWORD '" +
                    password + "' CHANGE NOT REQUIRED",
                    "GRANT ROLE " + role + " TO " + name,
                ]
            for statement in statements:
                query(admin, statement, db="system")
            write_state = (
                "MATCH (s:FleetProjectionState {name:'canonical'}) "
                "SET s.expires_at_ts=$exp RETURN s.expires_at_ts AS exp"
            )
            write_receipt = (
                "CREATE (:RuntimeProjectionApprovalReceipt "
                "{approval_id:$aid,generation:644,"
                "source_receipt_sha256:$digest})"
            )
            denials = {}
            for name in ("fixture_p0_api", "fixture_p0_worker"):
                with GraphDatabase.driver(
                        uri, auth=(name, user_passwords[name]),
                        connection_timeout=3) as client:
                    read = query(client,
                        "MATCH (s:FleetProjectionState {name:'canonical'}) "
                        "RETURN s.generation AS generation")
                    if read != [{"generation": 643}]:
                        raise RuntimeError("READER_CANNOT_READ_CANONICAL")
                    denials[name] = {
                        "canonical_expiry_set": must_deny(
                            client, write_state, params={"exp": 999999}),
                        "approval_receipt_create": must_deny(
                            client, write_receipt,
                            params={"aid": "not-authorized",
                                    "digest": "arbitrary"}),
                    }
            with GraphDatabase.driver(
                    uri, auth=("fixture_p0_broker",
                               user_passwords["fixture_p0_broker"]),
                    connection_timeout=3) as broker:
                # Deliberately unsafe: scoped DB role alone cannot validate
                # Ed25519 signatures. This must NOT count as approval proof.
                query(broker, write_state, params={"exp": 987654})
                query(broker, write_receipt, params={
                    "aid": "synthetic-unsigned-forgery",
                    "digest": "UNSIGNED_ARBITRARY_DIGEST"})
            with GraphDatabase.driver(
                    uri, auth=("fixture_p0_broker",
                               user_passwords["fixture_p0_broker"]),
                    connection_timeout=3) as broker:
                duplicate_receipt_denied = must_deny(
                    broker, write_receipt,
                    params={"aid":"synthetic-unsigned-forgery",
                            "digest":"another-unsigned-digest"})
                # Deliberate high-water negative: graph property grants
                # cannot guarantee generation monotonicity without an
                # independently enforced transaction authority.
                query(broker,
                    "MATCH (s:FleetProjectionState {name:'canonical'}) "
                    "SET s.generation=642")
            duplicate_canonical_denied = must_deny(admin,
                "CREATE (:FleetProjectionState {name:'canonical',generation:0})")
            state = query(admin,
                "MATCH (s:FleetProjectionState {name:'canonical'}) "
                "RETURN s.generation AS generation,"
                "s.expires_at_ts AS expires_at_ts")
            receipt_count = query(admin,
                "MATCH (r:RuntimeProjectionApprovalReceipt) "
                "RETURN count(r) AS n")[0]["n"]
            if (state != [{"generation": 642, "expires_at_ts": 987654}]
                    or receipt_count != 1
                    or not duplicate_receipt_denied
                    or not duplicate_canonical_denied):
                raise RuntimeError("RESOURCE_SIDE_NEGATIVES_NOT_REPRODUCED")
            return {
                "schema": "assistx-p0-neo4j-broker-rbac-physical-v1",
                "result": "PASS_RESOURCE_PRIVILEGE_SEPARATION_ONLY",
                "image": IMAGE,
                "isolated_internal_network": True,
                "host_port_published": False,
                "persistent_user_mounts": False,
                "denied_client_roles": denials,
                "broker_can_write_approval_properties": True,
                "broker_arbitrary_unsigned_digest_negative_reproduced": True,
                "db_receipt_id_uniqueness_denied_replay": duplicate_receipt_denied,
                "db_canonical_name_uniqueness_denied_duplicate": duplicate_canonical_denied,
                "broker_generation_rollback_counterexample_reproduced": True,
                "database_role_cannot_validate_ed25519": True,
                "trusted_broker_custody_proven": False,
                "resource_side_signature_validation_proven": False,
                "production_credential_rotation_authorized": False,
                "production_network_change_authorized": False,
                "production_admission_authorized": False,
                "elapsed_seconds": round(time.monotonic()-start, 2),
            }
    finally:
        if container_created:
            try:
                docker("rm", "-fv", container, timeout=18)
            except Exception:
                pass
        if network_created:
            try:
                docker("network", "rm", network, timeout=18)
            except Exception:
                pass


if __name__ == "__main__":
    try:
        print(json.dumps(run(), sort_keys=True))
    except Exception as exc:
        print(json.dumps({
            "schema": "assistx-p0-neo4j-broker-rbac-physical-v1",
            "result": "UNVERIFIED",
            "error_class": type(exc).__name__,
            "production_admission_authorized": False,
        }, sort_keys=True))
        raise SystemExit(2) from None
