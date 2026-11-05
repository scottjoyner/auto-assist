"""No-network Neo4j schema/runtime privilege-split regression tests.

All evidence is synthetic. Tests do not import AssistX, create DDL, contact
a DBMS, access local environment secrets or touch production containers.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
READINESS_PATH = ROOT / "src" / "assistx" / "schema_readiness.py"
NEO_PATH = ROOT / "src" / "assistx" / "neo4j_client.py"
API_PATH = ROOT / "src" / "assistx" / "api.py"
CLI_PATH = ROOT / "src" / "assistx" / "cli.py"

spec = importlib.util.spec_from_file_location("schema_readiness_tested", READINESS_PATH)
readiness = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(readiness)


def _operations():
    tree = ast.parse(NEO_PATH.read_text(encoding="utf-8"))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "Neo4jClient")
    function = next(node for node in klass.body if isinstance(node, ast.FunctionDef)
                    and node.name == "schema_operations")
    assignment = next(node for node in function.body if isinstance(node, ast.Assign)
                      and any(isinstance(name, ast.Name) and name.id == "cypher"
                              for name in node.targets))
    return ast.literal_eval(assignment.value)


class SyntheticSession:
    def __init__(self, *, missing_constraint=None, missing_index=None,
                 offline_index=None, exception=None):
        self.calls = []
        self.exception = exception
        self.constraints, self.indexes = readiness.expected_schema(_operations())
        self.missing_constraint = missing_constraint
        self.missing_index = missing_index
        self.offline_index = offline_index

    def run(self, statement):
        self.calls.append(statement)
        if self.exception:
            raise self.exception
        if statement == readiness.SHOW_CONSTRAINTS:
            return [
                dict(type="UNIQUENESS", entityType="NODE",
                     labelsOrTypes=[lab], properties=[field])
                for lab, field in sorted(self.constraints)
                if (lab, field) != self.missing_constraint
            ]
        if statement == readiness.SHOW_INDEXES:
            return [
                dict(type="RANGE", entityType="NODE",
                     labelsOrTypes=[lab], properties=[field],
                     state="POPULATING" if (lab, field) == self.offline_index
                     else "ONLINE")
                for lab, field in sorted(self.indexes)
                if (lab, field) != self.missing_index
            ]
        raise AssertionError("unexpected non-readonly query")


class SchemaRuntimeReadonlyTests(unittest.TestCase):
    def test_pinned_full_catalog(self):
        ops = _operations()
        cons, ix = readiness.expected_schema(ops)
        self.assertEqual((len(ops), len(cons), len(ix)), (139, 53, 86))
        self.assertIn(("ServiceEndpoint", "endpoint_id"), cons)

    def test_all_ready_queries_only_two_readonly_show_commands(self):
        fake = SyntheticSession()
        report = readiness.inspect_schema_readiness(fake, _operations())
        self.assertEqual(fake.calls, [
            readiness.SHOW_CONSTRAINTS, readiness.SHOW_INDEXES
        ])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["production_release_authorized"])
        self.assertEqual(report["verified_constraints"], 53)
        self.assertEqual(report["verified_online_indexes"], 86)

    def test_known_production_endpoint_uniqueness_gap_holds(self):
        fake = SyntheticSession(missing_constraint=("ServiceEndpoint", "endpoint_id"))
        with self.assertRaisesRegex(readiness.SchemaReadinessHold, "missing_constraints=1"):
            readiness.inspect_schema_readiness(fake, _operations())
        self.assertTrue(all(query.startswith("SHOW ") for query in fake.calls))

    def test_missing_index_holds(self):
        fake = SyntheticSession(missing_index=("Task", "status"))
        with self.assertRaisesRegex(readiness.SchemaReadinessHold, "missing_online_indexes=1"):
            readiness.inspect_schema_readiness(fake, _operations())

    def test_populating_index_holds(self):
        fake = SyntheticSession(offline_index=("Task", "status"))
        with self.assertRaises(readiness.SchemaReadinessHold):
            readiness.inspect_schema_readiness(fake, _operations())

    def test_unavailable_schema_metadata_sanitizes_exception(self):
        fake = SyntheticSession(exception=RuntimeError("private://host:secret-user"))
        with self.assertRaises(readiness.SchemaReadinessHold) as caught:
            readiness.inspect_schema_readiness(fake, _operations())
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_unknown_or_duplicate_catalog_rejected_without_show(self):
        bad = _operations() + [
            "CALL dbms.security.clearAuthCache()"
        ]
        fake = SyntheticSession()
        with self.assertRaises(readiness.SchemaReadinessHold):
            readiness.inspect_schema_readiness(fake, bad)
        self.assertEqual(fake.calls, [])
        dup = _operations() + [_operations()[0]]
        with self.assertRaises(readiness.SchemaReadinessHold):
            readiness.expected_schema(dup)

    def test_partial_catalog_is_always_hold(self):
        with self.assertRaises(readiness.SchemaReadinessHold):
            readiness.expected_schema(_operations()[:-1])

    def test_runtime_role_pin_rejects_shared_admin_and_unreviewed_roles(self):
        class RolesSession:
            def __init__(self, roles):
                self.roles = roles
                self.queries = []

            def run(self, query):
                self.queries.append(query)
                if query == readiness.SHOW_CURRENT_USER:
                    return [{"roles": self.roles}]
                raise AssertionError("unexpected graph command")

        good = RolesSession(["assistx_runtime", "PUBLIC"])
        report = readiness.inspect_runtime_principal(good, "assistx_runtime")
        self.assertEqual(good.queries, [readiness.SHOW_CURRENT_USER])
        self.assertTrue(report["pinned_runtime_role_observed"])
        self.assertFalse(report["native_privilege_isolation_proven"])
        self.assertFalse(report["production_release_authorized"])
        for names in (
            ["admin"], ["assistx_runtime", "admin"],
            ["assistx_runtime", "editor"], [], ["PUBLIC"],
            ["assistx_runtime", "assistx_runtime"],
        ):
            with self.subTest(roles=names), self.assertRaises(readiness.SchemaReadinessHold):
                readiness.inspect_runtime_principal(
                    RolesSession(names), "assistx_runtime"
                )

    def test_unset_builtin_or_malformed_role_pin_fails_before_query(self):
        class NoQuery:
            def run(self, query):
                raise AssertionError("must not query without valid pinned role")

        for pin in ("", "admin", "reader", "publisher", "public", "A",
                    "assistx runtime", "assistx_runtime;DELETE"):
            with self.subTest(pin=pin), self.assertRaises(readiness.SchemaReadinessHold):
                readiness.inspect_runtime_principal(NoQuery(), pin)

    def test_role_query_failure_is_sanitized(self):
        class Fails:
            def run(self, query):
                raise RuntimeError("bolt://private-host:7687 secret")
        with self.assertRaises(readiness.SchemaReadinessHold) as caught:
            readiness.inspect_runtime_principal(Fails(), "assistx_runtime")
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("private-host", str(caught.exception))

    def test_client_runtime_verification_enforces_role_before_catalog(self):
        tree = ast.parse(NEO_PATH.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "Neo4jClient")
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "verify_runtime_schema_readonly")
        body = ast.unparse(fn)
        self.assertIn("ASSISTX_NEO4J_RUNTIME_ROLE", body)
        self.assertLess(
            body.index("inspect_runtime_principal("),
            body.index("inspect_schema_readiness(")
        )
        self.assertIn("default_access_mode", body)

    def test_api_lifespan_has_no_ddl_and_never_swallows_readonly_mismatch(self):
        tree = ast.parse(API_PATH.read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "lifespan")
        t = ast.unparse(node)
        self.assertNotIn("ensure_schema(", t)
        self.assertIn("verify_runtime_schema_readonly()", t)
        self.assertNotIn("schema initialization warning at startup", t)
        # The verification call must appear in its own try/finally, not an
        # except Exception: pass that silently starts unverified services.
        self.assertTrue(any(isinstance(n, ast.Try) and not n.handlers
                            and any(isinstance(a, ast.Call)
                                    and isinstance(a.func, ast.Attribute)
                                    and a.func.attr == "verify_runtime_schema_readonly"
                                    for stmt in n.body for a in ast.walk(stmt))
                            for n in ast.walk(node)))

    def test_no_other_application_source_invokes_runtime_schema_ddl(self):
        # Scan every application module: new startup/recovery/model call sites
        # cannot silently reintroduce elevated DDL after this split.
        app_root = ROOT / "src" / "assistx"
        offenders = []
        for path in sorted(app_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"ensure_schema", "ensure_indexes"}:
                    continue
                ancestor = node
                while ancestor in parents and not isinstance(
                    ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    ancestor = parents[ancestor]
                fn = ancestor.name if isinstance(
                    ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) else None
                if (path.name == "cli.py" and fn == "init_cmd"
                        and node.func.attr == "ensure_schema"):
                    continue
                if (path.name == "neo4j_client.py" and fn == "ensure_indexes"
                        and node.func.attr == "ensure_schema"):
                    continue
                offenders.append((str(path.relative_to(app_root)), fn, node.func.attr))
        self.assertEqual(offenders, [], "unreviewed implicit schema DDL caller")

    def test_recovery_shadow_does_not_create_schema_or_swallow_mismatch(self):
        tree = ast.parse(
            (ROOT / "src" / "assistx" / "recovery_mode.py").read_text(
                encoding="utf-8"
            )
        )
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "recovery_shadow_lifespan")
        body = ast.unparse(fn)
        self.assertNotIn("ensure_schema(", body)
        self.assertIn("verify_runtime_schema_readonly()", body)
        self.assertNotIn("recovery shadow schema check failed", body)
        # A missing schema must not silently start the original app's
        # inherited API handlers through a swallowed Exception.
        self.assertFalse(any(
            isinstance(node, ast.ExceptHandler)
            for node in ast.walk(fn)
        ))

    def test_schema_creation_requires_explicit_separate_operator_intent(self):
        tree = ast.parse(NEO_PATH.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "Neo4jClient")
        func = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                    and n.name == "ensure_schema")
        self.assertIn("if explicit_operator_migration is not True:", ast.unparse(func))
        cli = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
        funcs = {n.name: ast.unparse(n) for n in cli.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("NEO4J_SCHEMA_OPERATOR_USER", funcs["init_cmd"])
        self.assertIn("NEO4J_SCHEMA_OPERATOR_PASSWORD", funcs["init_cmd"])
        self.assertIn("explicit_operator_migration=True", funcs["init_cmd"])
        self.assertNotIn("ensure_schema(", funcs["run_all"])
        self.assertIn("verify_runtime_schema_readonly()", funcs["run_all"])


if __name__ == "__main__":
    unittest.main()
