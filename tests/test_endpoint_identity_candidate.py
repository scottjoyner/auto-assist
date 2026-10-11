"""Offline, no-database contract tests for unwired canonical identity writer."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "src/assistx/endpoint_identity_candidate.py"
)
spec = importlib.util.spec_from_file_location("assistx_endpoint_identity_candidate", SOURCE)
assert spec is not None and spec.loader is not None
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)

CONSTRAINT = {
    "type": "UNIQUENESS", "entityType": "NODE",
    "labelsOrTypes": ["ServiceEndpoint"], "properties": ["endpoint_id"],
}


def service(
    *, identity="same-node", model=False,
    service_type="model_endpoint", node_id="x1",
    model_id=None, owners=None,
):
    return {
        "ref": identity,
        "labels": ["ServiceEndpoint", "ModelEndpoint"] if model else ["ServiceEndpoint"],
        "service_type": service_type, "node_id": node_id,
        "model_endpoint_id": model_id, "owners": owners if owners is not None else ["x1"],
    }


class FakeTx:
    def __init__(self, *, constraints=None, services=None, models=None,
                 write_result=None, serve_result=None, after_lock_services=None):
        self.after_lock_services = after_lock_services
        self.constraints = [CONSTRAINT] if constraints is None else constraints
        self.services = [] if services is None else services
        self.models = [] if models is None else models
        self.write_result = [{"ref": "same-node"}] if write_result is None else write_result
        self.serve_result = [{"linked": 1}] if serve_result is None else serve_result
        self.queries = []

    def run(self, statement, **kwargs):
        self.queries.append((statement, kwargs))
        if statement == contract.CONSTRAINT_QUERY:
            return iter(self.constraints)
        if statement == contract.SERVICE_IDENTITY_QUERY:
            return iter(self.services)
        if statement == contract.MODEL_IDENTITY_QUERY:
            return iter(self.models)
        if statement == contract.LOCK_QUERY:
            if self.after_lock_services is not None:
                self.services = self.after_lock_services
            return iter([{"ref": "same-node"}])
        if statement in (contract.UPSERT_QUERY, contract.SERVICE_UPSERT_QUERY):
            return iter(self.write_result)
        if statement == contract.SERVES_QUERY:
            return iter(self.serve_result)
        raise AssertionError("unexpected query")


class FakeSession:
    def __init__(self, tx):
        self.tx = tx
        self.writes_started = 0

    def execute_write(self, method, *args):
        self.writes_started += 1
        return method(self.tx, *args)


def call(tx, **kwargs):
    return contract.candidate_register_model_endpoint(
        FakeSession(tx), endpoint_id="endpoint-1", node_id="x1",
        **kwargs,
    )


class IdentityCandidateOfflineTests(unittest.TestCase):
    def test_new_model_identity_with_constraint(self):
        tx = FakeTx()
        result = call(tx, safe_properties={"provider": "synthetic"}, model_ids=("model-1",))
        self.assertEqual(result["status"], "CANDIDATE_REGISTERED_IN_ISOLATED_GRAPH_ONLY")
        self.assertFalse(result["production_release_authorized"])
        self.assertEqual([s for s, _ in tx.queries][-2:], [
            contract.UPSERT_QUERY, contract.SERVES_QUERY,
        ])
        all_queries = [s for s, _ in tx.queries]
        self.assertLess(all_queries.index(contract.LOCK_QUERY),
                        all_queries.index(contract.UPSERT_QUERY))
        self.assertEqual(all_queries.count(contract.SERVICE_IDENTITY_QUERY), 2)

    def test_existing_service_model_type_can_be_promoted(self):
        tx = FakeTx(services=[service()])
        call(tx)
        self.assertIn(contract.UPSERT_QUERY, [s for s, _ in tx.queries])

    def test_existing_dual_labeled_model_is_idempotent(self):
        tx = FakeTx(
            services=[service(model=True, model_id="endpoint-1")],
            models=[{"ref": "same-node", "endpoint_id": "endpoint-1"}],
        )
        call(tx, model_ids=("m1", "m2"))
        self.assertEqual([s for s, _ in tx.queries].count(contract.SERVES_QUERY), 2)

    def test_missing_schema_constraint_has_zero_writes(self):
        tx = FakeTx(constraints=[])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)
        self.assertEqual(len(tx.queries), 1)

    def test_other_label_constraint_cannot_authorize(self):
        tx = FakeTx(constraints=[{**CONSTRAINT, "labelsOrTypes": ["ModelEndpoint"]}])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)
        self.assertEqual(len(tx.queries), 1)

    def test_duplicate_service_records_fail_before_write(self):
        tx = FakeTx(services=[service(identity="one"), service(identity="two")])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)
        self.assertNotIn(contract.UPSERT_QUERY, [s for s, _ in tx.queries])

    def test_duplicate_model_identity_fails_before_write(self):
        tx = FakeTx(models=[{"ref": "one"}, {"ref": "two"}])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)
        self.assertNotIn(contract.UPSERT_QUERY, [s for s, _ in tx.queries])

    def test_distinct_model_and_service_nodes_are_not_reconciled(self):
        tx = FakeTx(
            services=[service(identity="service")],
            models=[{"ref": "model", "endpoint_id": "endpoint-1"}],
        )
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)
        self.assertNotIn(contract.UPSERT_QUERY, [s for s, _ in tx.queries])

    def test_orphan_model_is_not_adopted(self):
        tx = FakeTx(models=[{"ref": "orphan", "endpoint_id": "endpoint-1"}])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)

    def test_existing_generic_service_cannot_be_retyped(self):
        tx = FakeTx(services=[service(service_type="generic")])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)

    def test_conflicting_swarm_owner_is_rejected(self):
        for row in (
            service(node_id="other"),
            service(owners=["other"]),
            service(owners=["x1", "other"]),
            service(owners=[None]),
        ):
            with self.subTest(row=row):
                tx = FakeTx(services=[row])
                with self.assertRaises(contract.EndpointIdentityHold):
                    call(tx)
                self.assertNotIn(contract.UPSERT_QUERY, [s for s, _ in tx.queries])

    def test_competing_owner_claim_after_lock_fails_before_upsert(self):
        tx = FakeTx(after_lock_services=[service(node_id="other", owners=["other"])])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)
        statements = [s for s, _ in tx.queries]
        self.assertIn(contract.LOCK_QUERY, statements)
        self.assertNotIn(contract.UPSERT_QUERY, statements)
        self.assertEqual(statements.count(contract.SERVICE_IDENTITY_QUERY), 2)

    def test_competing_ordinary_service_claim_after_lock_fails(self):
        tx = FakeTx(after_lock_services=[
            service(service_type="generic", node_id="x1")
        ])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)
        self.assertNotIn(contract.UPSERT_QUERY, [s for s, _ in tx.queries])

    def test_unknown_lock_result_fails_without_upsert(self):
        class MissingLockTx(FakeTx):
            def run(self, statement, **kwargs):
                if statement == contract.LOCK_QUERY:
                    self.queries.append((statement, kwargs))
                    return iter([])
                return super().run(statement, **kwargs)
        tx = MissingLockTx()
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)
        self.assertNotIn(contract.UPSERT_QUERY, [s for s, _ in tx.queries])

    def test_conflicting_model_id_is_rejected(self):
        tx = FakeTx(services=[service(model=True, model_id="different")])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)

    def test_model_label_missing_index_identity_is_rejected(self):
        tx = FakeTx(services=[service(model=True, model_id="endpoint-1")])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)

    def test_incorrect_model_property_is_rejected(self):
        tx = FakeTx(
            services=[service(model=True, model_id="endpoint-1")],
            models=[{"ref": "same-node", "endpoint_id": "different"}],
        )
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)

    def test_allowlist_never_overwrites_identity_fields(self):
        for value in (
            {"endpoint_id": "replacement"},
            {"node_id": "other"},
            {"service_type": "generic"},
            {"service": "unexpected"},
            {"provider": {"nested": "unsafe"}},
        ):
            with self.subTest(value=value):
                tx = FakeTx()
                session = FakeSession(tx)
                with self.assertRaises(contract.EndpointIdentityHold):
                    contract.candidate_register_model_endpoint(
                        session, endpoint_id="endpoint-1", node_id="x1",
                        safe_properties=value,
                    )
                self.assertEqual(session.writes_started, 0)

    def test_identifiers_must_be_bounded_and_nonempty(self):
        for identifier in ("", " leading", "trailing ", "bad\nid", "x"*257, None):
            with self.subTest(identifier=repr(identifier)[:30]):
                session = FakeSession(FakeTx())
                with self.assertRaises(contract.EndpointIdentityHold):
                    contract.candidate_register_model_endpoint(
                        session, endpoint_id=identifier, node_id="x1",
                    )
                self.assertEqual(session.writes_started, 0)

    def test_model_ids_are_bounded_and_unique(self):
        for models in ("string", ["duplicate", "duplicate"], ["x"]*33, [None]):
            with self.subTest(models=str(models)[:30]):
                session = FakeSession(FakeTx())
                with self.assertRaises(contract.EndpointIdentityHold):
                    contract.candidate_register_model_endpoint(
                        session, endpoint_id="endpoint-1", node_id="x1",
                        model_ids=models,
                    )
                self.assertEqual(session.writes_started, 0)

    def test_missing_upsert_result_ref_causes_transaction_failure(self):
        tx = FakeTx(write_result=[{"ref": None}])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx)

    def test_missing_serves_relationship_result_causes_transaction_failure(self):
        tx = FakeTx(serve_result=[])
        with self.assertRaises(contract.EndpointIdentityHold):
            call(tx, model_ids=("m1",))

    def test_no_delete_or_schema_ddl_in_candidate_writes(self):
        for q in (contract.LOCK_QUERY, contract.UPSERT_QUERY, contract.SERVES_QUERY):
            upper = " ".join(q.upper().split())
            for marker in ("DETACH DELETE", " DELETE ", "DROP ", "CREATE CONSTRAINT",
                           "CREATE INDEX", "REMOVE "):
                with self.subTest(marker=marker):
                    self.assertNotIn(marker, upper)
        self.assertIn("MERGE (E:SERVICEENDPOINT {ENDPOINT_ID:$ENDPOINT_ID})",
                      " ".join(contract.UPSERT_QUERY.upper().split()))
        self.assertIn("SET E:MODELENDPOINT", contract.UPSERT_QUERY.upper())

    def test_module_is_not_imported_in_deployed_upsert_source(self):
        code = (
            Path(__file__).resolve().parents[1]
            / "src/assistx/swarm_core.py"
        ).read_text()
        self.assertNotIn("endpoint_identity_candidate", code)




def call_service(tx, *, service_type="model_endpoint", **kwargs):
    return contract.candidate_register_service_endpoint(
        FakeSession(tx), endpoint_id="endpoint-1",
        node_id="x1", service_type=service_type, **kwargs,
    )


class ServiceCandidateOfflineTests(unittest.TestCase):
    def test_model_kind_service_first_creates_shared_key(self):
        tx = FakeTx()
        result = call_service(tx, safe_properties={"port": 1235})
        self.assertEqual(result["status"], "CANDIDATE_SERVICE_REGISTERED_IN_ISOLATED_GRAPH_ONLY")
        self.assertFalse(result["production_release_authorized"])
        statements = [query for query, _ in tx.queries]
        self.assertIn(contract.LOCK_QUERY, statements)
        self.assertLess(statements.index(contract.LOCK_QUERY),
                        statements.index(contract.SERVICE_UPSERT_QUERY))
        self.assertEqual(statements.count(contract.SERVICE_IDENTITY_QUERY), 2)

    def test_service_candidate_can_follow_compatible_model(self):
        tx = FakeTx(
            services=[service(model=True, model_id="endpoint-1")],
            models=[{"ref": "same-node", "endpoint_id": "endpoint-1"}],
        )
        call_service(tx)
        self.assertIn(contract.SERVICE_UPSERT_QUERY, [q for q, _ in tx.queries])

    def test_generic_service_new_key_is_valid_but_not_model_identity(self):
        tx = FakeTx()
        call_service(tx, service_type="generic", safe_properties={"service": "api"})
        self.assertIn(contract.SERVICE_UPSERT_QUERY, [q for q, _ in tx.queries])

    def test_generic_service_cannot_retype_model_endpoint(self):
        tx = FakeTx(
            services=[service(model=True, model_id="endpoint-1")],
            models=[{"ref": "same-node", "endpoint_id": "endpoint-1"}],
        )
        with self.assertRaises(contract.EndpointIdentityHold):
            call_service(tx, service_type="generic")
        self.assertNotIn(contract.SERVICE_UPSERT_QUERY, [q for q, _ in tx.queries])

    def test_model_service_cannot_retype_generic_endpoint(self):
        tx = FakeTx(services=[service(service_type="generic")])
        with self.assertRaises(contract.EndpointIdentityHold):
            call_service(tx)
        self.assertNotIn(contract.SERVICE_UPSERT_QUERY, [q for q, _ in tx.queries])

    def test_service_foreign_ownership_is_not_overwritten(self):
        tx = FakeTx(services=[service(node_id="other", owners=["other"])])
        with self.assertRaises(contract.EndpointIdentityHold):
            call_service(tx)
        self.assertNotIn(contract.SERVICE_UPSERT_QUERY, [q for q, _ in tx.queries])

    def test_service_foreign_owner_appears_only_after_lock(self):
        tx = FakeTx(after_lock_services=[service(node_id="other", owners=["other"])])
        with self.assertRaises(contract.EndpointIdentityHold):
            call_service(tx)
        self.assertIn(contract.LOCK_QUERY, [q for q, _ in tx.queries])
        self.assertNotIn(contract.SERVICE_UPSERT_QUERY, [q for q, _ in tx.queries])

    def test_service_missing_unique_constraint_denied(self):
        tx = FakeTx(constraints=[])
        with self.assertRaises(contract.EndpointIdentityHold):
            call_service(tx)
        self.assertEqual(len(tx.queries), 1)

    def test_service_unsafe_metadata_denied_before_transaction(self):
        for field in ("endpoint_id", "model_endpoint_id", "provider", "service_type"):
            with self.subTest(field=field):
                session = FakeSession(FakeTx())
                with self.assertRaises(contract.EndpointIdentityHold):
                    contract.candidate_register_service_endpoint(
                        session, endpoint_id="endpoint-1", node_id="x1",
                        service_type="model_endpoint",
                        safe_properties={field: "unsafe"},
                    )
                self.assertEqual(session.writes_started, 0)

    def test_service_orphan_model_identity_is_denied(self):
        tx = FakeTx(models=[{"ref": "orphan", "endpoint_id": "endpoint-1"}])
        with self.assertRaises(contract.EndpointIdentityHold):
            call_service(tx)

    def test_service_writer_no_destructive_commands(self):
        sql = " ".join(contract.SERVICE_UPSERT_QUERY.upper().split())
        self.assertIn("MERGE (E:SERVICEENDPOINT {ENDPOINT_ID:$ENDPOINT_ID})", sql)
        self.assertIn("MERGE (N)-[:EXPOSES]->(E)", sql)
        for forbidden in (" DELETE ", " DETACH ", " DROP ", "CREATE CONSTRAINT",
                          "REMOVE ", "SET E:MODELENDPOINT"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, sql)


if __name__ == "__main__":
    unittest.main()
