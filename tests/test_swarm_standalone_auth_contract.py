"""Source-executed, no-network, no-app-import authorization regression.

Executes the real AST of the two swarm auth dependencies with synthetic
stand-in types. This tests behavior without Neo4j, FastAPI, credentials, model
runtimes, shell commands, or production host access.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWARM = ROOT / "src" / "assistx" / "swarm_routes.py"
VOICE = ROOT / "src" / "assistx" / "voice_routes.py"


class DummyHttpException(Exception):
    def __init__(self, status_code: int, detail: str = ""):
        self.status_code = status_code
        super().__init__(detail)


class Request:
    pass


class HTTPBasicCredentials:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password


def _dependency(_arg):
    return None


def _exec_real_auth_functions() -> dict:
    tree = ast.parse(SWARM.read_text(encoding="utf-8"))
    found = {
        n.name: n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    names = ("_default_auth", "_optional_operator_auth")
    assert all(name in found for name in names)
    code = "from __future__ import annotations\n" + "\n\n".join(
        ast.unparse(found[name]) for name in names
    )
    scope = {
        "__builtins__": __builtins__,
        "Request": Request, "HTTPBasicCredentials": HTTPBasicCredentials,
        "HTTPException": DummyHttpException,
        "Depends": _dependency, "security": object(),
        "_injected_auth_dependency": None,
    }
    exec(compile(code, str(SWARM), "exec"), scope)
    return scope


class SwarmAuthFailsClosed(unittest.TestCase):
    def setUp(self):
        self.sc = _exec_real_auth_functions()
        self.req = Request()

    def test_no_basic_or_injection_is_401_never_system(self):
        with self.assertRaises(DummyHttpException) as ctx:
            self.sc["_default_auth"](self.req, None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_fake_basic_without_injected_verifier_is_401(self):
        with self.assertRaises(DummyHttpException) as ctx:
            self.sc["_default_auth"](
                self.req, HTTPBasicCredentials("operator", "invalid")
            )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_missing_injected_verifier_never_authenticates_voice_basic(self):
        for creds in (None, HTTPBasicCredentials("operator", "guess")):
            with self.subTest(creds=bool(creds)):
                self.assertIsNone(
                    self.sc["_optional_operator_auth"](self.req, creds)
                )

    def test_verified_operator_delegation_is_preserved(self):
        received = []

        def verified(request, credentials):
            received.append((request, credentials))
            return "verified-synthetic-operator"

        self.sc["_injected_auth_dependency"] = verified
        credential = HTTPBasicCredentials("user", "synthetic")
        self.assertEqual(
            self.sc["_default_auth"](self.req, credential),
            "verified-synthetic-operator",
        )
        self.assertEqual(
            self.sc["_optional_operator_auth"](self.req, credential),
            "verified-synthetic-operator",
        )
        self.assertEqual(received, [(self.req, credential)] * 2)

    def test_invalid_operator_delegation_is_not_silenced(self):
        def invalid(_request, _credentials):
            raise DummyHttpException(status_code=401)

        self.sc["_injected_auth_dependency"] = invalid
        with self.assertRaises(DummyHttpException) as ctx:
            self.sc["_default_auth"](self.req, None)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIsNone(self.sc["_optional_operator_auth"](self.req, None))

    def test_blank_injected_identity_does_not_grant_privilege(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.sc["_injected_auth_dependency"] = lambda *_args: value
                with self.assertRaises(DummyHttpException) as ctx:
                    self.sc["_default_auth"](self.req, None)
                self.assertEqual(ctx.exception.status_code, 401)
                self.assertIsNone(
                    self.sc["_optional_operator_auth"](self.req, None)
                )

    def test_non401_authorization_denial_is_not_swallowed(self):
        def forbidden(_request, _credentials):
            raise DummyHttpException(status_code=403)

        self.sc["_injected_auth_dependency"] = forbidden
        with self.assertRaises(DummyHttpException) as ctx:
            self.sc["_optional_operator_auth"](self.req, None)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_voice_transport_requires_signature_without_operator(self):
        source = ast.parse(VOICE.read_text(encoding="utf-8"))
        fn = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_authenticate_transport")
        code = ast.unparse(fn)
        self.assertIn("if operator_user:", code)
        self.assertIn("_verify_transport_signature(raw_body, signature)", code)

    def test_trace_and_write_routes_still_use_verified_dependency(self):
        source = ast.parse(SWARM.read_text(encoding="utf-8"))
        routed = {
            n.name: ast.unparse(n)
            for n in source.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for route in (
            "api_list_traces", "api_get_trace", "api_record_trace_event",
            "api_events", "api_fail_task", "api_release_expired_leases",
            "api_delete_model_endpoint", "api_register_model_endpoint",
        ):
            with self.subTest(route=route):
                self.assertIn("Depends(_default_auth)", routed[route])


if __name__ == "__main__":
    unittest.main()
