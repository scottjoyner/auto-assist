"""Opt-in REAL Neo4j integration for canonical approval CAS and rollback.

NEVER run against production. Runner must use a disposable, empty Neo4j
instance published only to localhost at a NON-DEFAULT test port and set
ASSISTX_TEST_DISPOSABLE_ACK=ephemeral-neo4j-only. No other credentials accepted.
The tests intentionally write synthetic nodes to that isolated database.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_projection_signed_apply_gate as G  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "canonical_apply_for_disposable_neo4j", ROOT / "scripts" / "approve-runtime-projection.py"
)
A = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = A
SPEC.loader.exec_module(A)
import test_runtime_projection_signed_apply_gate as F  # noqa: E402


def disposable_environment() -> tuple[str, str, str] | None:
    if os.environ.get("ASSISTX_TEST_DISPOSABLE_ACK") != "ephemeral-neo4j-only":
        return None
    uri = os.environ.get("ASSISTX_TEST_NEO4J_URI", "")
    parts = urlsplit(uri)
    if (
        parts.scheme != "bolt" or parts.hostname != "127.0.0.1"
        or not isinstance(parts.port, int)
        or parts.port in (7687, 17687) or not 1024 < parts.port < 65535
        or parts.username or parts.password or parts.path
    ):
        return None
    user = os.environ.get("ASSISTX_TEST_NEO4J_USER", "")
    password = os.environ.get("ASSISTX_TEST_NEO4J_PASSWORD", "")
    if user != "neo4j" or not password or len(password) < 8:
        return None
    return uri, user, password


@unittest.skipUnless(disposable_environment(), "isolated disposable Neo4j + ack required")
class TestRealNeo4jCanonicalApply(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = disposable_environment()
        if env is None:
            raise unittest.SkipTest("disposable Neo4j ACK missing")
        uri, user, password = env
        cls.driver = GraphDatabase.driver(
            uri, auth=(user, password), connection_timeout=4,
            max_connection_pool_size=8, max_transaction_retry_time=3,
        )
        cls.driver.verify_connectivity()
        with cls.driver.session(database="neo4j") as session:
            # The test runner must create an EMPTY private instance. This is
            # not a substitute for isolation by Docker network/credentials.
            existing = session.run("MATCH (n) RETURN count(n) AS count").single()
            if existing["count"]:
                raise AssertionError("test Neo4j MUST be empty before test execution")
            session.run(
                "CREATE CONSTRAINT test_canonical_identity IF NOT EXISTS "
                "FOR (s:FleetProjectionState) REQUIRE s.name IS UNIQUE"
            ).consume()
            session.run(
                "CREATE CONSTRAINT test_receipt_identity IF NOT EXISTS "
                "FOR (r:RuntimeProjectionApprovalReceipt) "
                "REQUIRE r.approval_id IS UNIQUE"
            ).consume()

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()

    def setUp(self):
        with self.driver.session(database="neo4j") as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
        self.source_key = F.Ed25519PrivateKey.generate()
        self.operator_key = F.Ed25519PrivateKey.generate()
        self.original_now = F.NOW
        F.NOW = int(time.time() * 1000)

    def tearDown(self):
        F.NOW = self.original_now

    def bundle(self, *, generation: int = 1, approval_id: str = "test_approval_A"):
        manifest = F.manifest()
        manifest["generation"] = generation
        manifest["expected_current_generation"] = generation - 1
        manifest["approval_id"] = approval_id
        receipt = F.package(manifest, self.source_key, self.operator_key)
        return A.validate_manifest(manifest), receipt

    def apply(self, manifest, receipt):
        with self.driver.session(database="neo4j") as s:
            return s.execute_write(
                A._apply_transaction, manifest, receipt,
                F.public(self.source_key), F.public(self.operator_key),
            )

    def counts(self):
        with self.driver.session(database="neo4j") as s:
            return dict(s.run(
                "MATCH (n) RETURN count(n) AS total, "
                "sum(CASE WHEN n:RuntimeInstance THEN 1 ELSE 0 END) AS runtime, "
                "sum(CASE WHEN n:RuntimeProjectionApprovalReceipt THEN 1 ELSE 0 END) "
                "AS receipts"
            ).single())

    def canonical(self):
        with self.driver.session(database="neo4j") as s:
            return s.run(
                "MATCH (s:FleetProjectionState {name:'canonical'}) "
                "RETURN s.generation AS generation, s.approval_id AS approval_id"
            ).single()

    def test_required_constraints_are_real_and_source_check_passes(self):
        with self.driver.session(database="neo4j") as s:
            items = s.run(
                "SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties "
                "RETURN type, labelsOrTypes, properties"
            ).data()
        G.require_uniqueness_constraints(items)

    def test_genuine_transaction_persists_original_observation_times(self):
        manifest, receipt = self.bundle()
        result = self.apply(manifest, receipt)
        self.assertEqual(result["generation"], 1)
        self.assertEqual(self.canonical()["generation"], 1)
        self.assertEqual(self.counts()["receipts"], 1)
        self.assertEqual(self.counts()["runtime"], 1)
        observation = receipt["source"]["payload"]["observations"][0]
        with self.driver.session(database="neo4j") as s:
            source_identity = s.run(
                "MATCH (r:RuntimeInstance {runtime_instance_id:'runtime_a'}) "
                "RETURN r.linux_boot_id AS boot_id, "
                "r.process_start_ticks AS process_start_ticks, "
                "r.process_model_sha256 AS process_model_sha256"
            ).single()
        self.assertEqual(source_identity["boot_id"], observation["linux_boot_id"])
        self.assertEqual(
            source_identity["process_start_ticks"], observation["process_start_ticks"]
        )
        self.assertEqual(
            source_identity["process_model_sha256"], observation["process_model_sha256"]
        )
        with self.driver.session(database="neo4j") as s:
            row = s.run(
                "MATCH (r:RuntimeInstance)-[:SERVES]->(m:LoadedModelInstance) "
                "MATCH (r)-[:HAS_CAPACITY_OBSERVATION]->(c:CapacityObservation) "
                "MATCH (a:AccessPath)-[:REACHES]->(r) "
                "RETURN r.observed_at_ts AS runtime_time, "
                "m.observed_at_ts AS model_time, "
                "c.observed_at_ts AS capacity_time, "
                "collect(a.observed_at_ts) AS path_times"
            ).single()
        self.assertEqual(row["runtime_time"], observation["physical_observed_at_ms"])
        self.assertEqual(row["model_time"], observation["model_observed_at_ms"])
        self.assertEqual(row["capacity_time"], observation["capacity_observed_at_ms"])
        self.assertEqual(sorted(row["path_times"]), sorted(
            p["canary_observed_at_ms"] for p in observation["paths"]
        ))

    def test_same_approval_id_and_generation_are_not_reapplied(self):
        manifest, receipt = self.bundle()
        self.apply(manifest, receipt)
        before = self.counts()
        with self.assertRaises(Exception):
            self.apply(manifest, receipt)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.canonical()["generation"], 1)

    def test_replay_approval_id_for_next_generation_rejected(self):
        manifest, receipt = self.bundle()
        self.apply(manifest, receipt)
        next_manifest, next_receipt = self.bundle(generation=2, approval_id="test_approval_A")
        with self.assertRaises(Exception):
            self.apply(next_manifest, next_receipt)
        self.assertEqual(self.canonical()["generation"], 1)
        self.assertEqual(self.counts()["receipts"], 1)

    def test_late_failure_rolls_back_retirement_and_approval_receipt(self):
        manifest, receipt = self.bundle()
        self.apply(manifest, receipt)
        next_manifest, next_receipt = self.bundle(
            generation=2, approval_id="test_approval_B"
        )
        class FailAfterGraphWrites:
            def __init__(self, tx):
                self.tx = tx

            def run(self, query, **params):
                if "MATCH (s:FleetProjectionState {name:'canonical'})\n        SET" in query:
                    raise RuntimeError("synthetic failure after source writes")
                return self.tx.run(query, **params)

        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with self.driver.session(database="neo4j") as s:
                s.execute_write(
                    lambda tx: A._apply_transaction(
                        FailAfterGraphWrites(tx), next_manifest, next_receipt,
                        F.public(self.source_key), F.public(self.operator_key),
                    )
                )
        self.assertEqual(self.canonical()["generation"], 1)
        self.assertEqual(self.counts()["receipts"], 1)
        with self.driver.session(database="neo4j") as s:
            current = s.run(
                "MATCH (r:RuntimeInstance) RETURN r.admitted AS admitted"
            ).single()
        self.assertIs(current["admitted"], True)

    def test_competing_generation_approvals_never_both_commit(self):
        # Multiple freshly emptied experimental generations exercise real
        # session contention; 1/1 is not sufficient confidence for CAS.
        for trial in range(8):
            with self.subTest(trial=trial):
                with self.driver.session(database="neo4j") as session:
                    session.run("MATCH (n) DETACH DELETE n").consume()
                left, lproof = self.bundle(
                    generation=1, approval_id=f"test_race_left_{trial}"
                )
                right, rproof = self.bundle(
                    generation=1, approval_id=f"test_race_right_{trial}"
                )
                barrier = threading.Barrier(2)

                def runner(m, receipt):
                    try:
                        barrier.wait(timeout=6)
                        self.apply(m, receipt)
                        return "committed"
                    except Exception:
                        return "refused"

                with ThreadPoolExecutor(max_workers=2) as workers:
                    futures = [workers.submit(runner, left, lproof),
                               workers.submit(runner, right, rproof)]
                    outcome = [f.result(timeout=20) for f in futures]
                self.assertEqual(outcome.count("committed"), 1, outcome)
                self.assertEqual(self.counts()["receipts"], 1)
                self.assertEqual(self.canonical()["generation"], 1)


if __name__ == "__main__":
    unittest.main()
