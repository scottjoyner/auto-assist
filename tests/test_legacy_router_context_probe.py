"""Synthetic regression tests for legacy router-context probes: no network or secrets."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib
from io import StringIO
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import legacy_router_context_probe as probe
from scripts import coordination_check

class Response:
    def __init__(self,code):
        self.status_code=code
        self.closed=False
        self.json_called=False
        self.text_called=False
        self.headers={"content-type":"application/json"}
    def close(self):self.closed=True
    def json(self):
        self.json_called=True
        return {"data":[]}
    @property
    def text(self):
        self.text_called=True
        return "SYNTHETIC_PRIVATE_BODY"

class Session:
    def __init__(self,code=401,fail=False):
        self.response=Response(code)
        self.fail=fail
        self.calls=[]
        self.closed=False
        self.trust_env=True
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        if self.fail:raise ConnectionError("synthetic network unavailable")
        return self.response
    def close(self):self.closed=True

def fake_requests_only_public():
    module=types.ModuleType("requests")
    calls=[]
    def get(url,**kw):
        if "/admin/" in url:
            raise AssertionError("Old script must not fetch private route directly")
        calls.append((url,kw))
        return Response(200)
    module.get=get
    return module,calls

class TestStatusClassification(unittest.TestCase):
    def test_exact_401_is_secured(self):
        x=probe.classify_router_context_status(401)
        self.assertEqual(x["status"],"secured")
        self.assertTrue(x["auth_required"])
        self.assertIsNone(x["has_context"])
        self.assertFalse(x["credentials_sent"])
        self.assertFalse(x["response_body_read"])
    def test_anonymous_200_is_exposure_not_healthy(self):
        x=probe.classify_router_context_status(200)
        self.assertEqual(x["status"],"anonymous_admin_exposure")
        self.assertFalse(x["auth_required"])
    def test_all_other_responses_do_not_pass(self):
        for code in (None,True,False,0,302,403,404,500,600,"401"):
            with self.subTest(code=code):
                self.assertEqual(probe.classify_router_context_status(code)["status"],"unverified")
    def test_probe_is_status_only_closes_resources(self):
        s=Session(401)
        x=probe.probe_router_context(lambda:s)
        self.assertEqual(x["status"],"secured")
        self.assertFalse(s.trust_env)
        self.assertEqual(s.calls,[(probe.ROUTER_CONTEXT_URL,{"timeout":5,"allow_redirects":False,"stream":True})])
        self.assertTrue(s.response.closed)
        self.assertTrue(s.closed)
        self.assertFalse(s.response.json_called)
        self.assertFalse(s.response.text_called)
    def test_probe_keeps_200_as_security_issue_without_reading_body(self):
        s=Session(200)
        x=probe.probe_router_context(lambda:s)
        self.assertEqual(x["status"],"anonymous_admin_exposure")
        self.assertFalse(s.response.json_called)
        self.assertTrue(s.response.closed)
    def test_network_error_cannot_pass_and_closes_session(self):
        s=Session(fail=True)
        x=probe.probe_router_context(lambda:s)
        self.assertEqual(x["status"],"unverified")
        self.assertTrue(s.closed)
        self.assertFalse(x["release_authorized"])
    def test_probe_never_uses_token_parameter(self):
        import inspect
        self.assertNotIn("AUTO_ROUTER_ADMIN_TOKEN",inspect.getsource(probe))
        self.assertNotIn("X-Admin-Token",inspect.getsource(probe))
        self.assertNotIn("response.json(",inspect.getsource(probe))

class TestCallers(unittest.TestCase):
    def test_coordination_secure_status_has_no_false_incident(self):
        req, public_calls=fake_requests_only_public()
        with mock.patch.dict(sys.modules,{"requests":req}),mock.patch.object(
            coordination_check,"probe_router_context",return_value=probe.classify_router_context_status(401)):
            summary=coordination_check.check_system_health()
        x=summary["integration"]["context_endpoint"]
        self.assertEqual(x["status"],"secured")
        self.assertEqual(x["http_status"],401)
        self.assertNotIn("auto-router admin context allows anonymous reads",summary["issues"])
        self.assertEqual(len(public_calls),3)
    def test_coordination_200_flags_security_issue(self):
        req,_=fake_requests_only_public()
        with mock.patch.dict(sys.modules,{"requests":req}),mock.patch.object(
            coordination_check,"probe_router_context",return_value=probe.classify_router_context_status(200)):
            summary=coordination_check.check_system_health()
        self.assertEqual(summary["integration"]["context_endpoint"]["status"],"anonymous_admin_exposure")
        self.assertIn("auto-router admin context allows anonymous reads",summary["issues"])
    def test_coordination_unverified_does_not_claim_empty_context(self):
        req,_=fake_requests_only_public()
        with mock.patch.dict(sys.modules,{"requests":req}),mock.patch.object(
            coordination_check,"probe_router_context",return_value=probe.classify_router_context_status(404)):
            summary=coordination_check.check_system_health()
        self.assertIsNone(summary["integration"]["context_endpoint"]["has_context"])
        self.assertIn("auto-router admin context authentication unverified",summary["issues"])
    def test_docs_sync_report_secured_without_direct_admin_get(self):
        mock_neo4j=types.ModuleType("neo4j")
        mock_neo4j.GraphDatabase=object
        req,public=fake_requests_only_public()
        with mock.patch.dict(sys.modules,{"requests":req,"neo4j":mock_neo4j}), mock.patch.object(
            probe,"probe_router_context",return_value=probe.classify_router_context_status(401)),redirect_stdout(StringIO()) as out:
            docs=importlib.import_module("scripts.sync_docs_to_neo4j")
            docs.verify_integration_endpoints()
        self.assertIn("secured (anonymous 401)",out.getvalue())
        self.assertEqual(len(public),3)
    def test_docs_sync_200_is_loudly_unsafe(self):
        mock_neo4j=types.ModuleType("neo4j");mock_neo4j.GraphDatabase=object
        req,_=fake_requests_only_public()
        with mock.patch.dict(sys.modules,{"requests":req,"neo4j":mock_neo4j}),mock.patch.object(
            probe,"probe_router_context",return_value=probe.classify_router_context_status(200)),redirect_stdout(StringIO()) as out:
            docs=importlib.import_module("scripts.sync_docs_to_neo4j")
            docs.verify_integration_endpoints()
        self.assertIn("UNSAFE anonymous 200",out.getvalue())

if __name__=="__main__":
    unittest.main(verbosity=2)
