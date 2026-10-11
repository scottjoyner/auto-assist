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
                 write_result=None, serve_result=None):
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
        if statement == contract.UPSERT_QUERY:
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
        for q in (contract.UPSERT_QUERY, contract.SERVES_QUERY):
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


if __name__ == "__main__":
    unittest.main()
