"""No-network security contract for the privileged AssistX Router fleet-report GET."""
from __future__ import annotations

import pytest
import requests

from assistx import router_admin_transport as t

TOKEN = "synthetic-only-in-tests"

class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = {"fleet_loadout_report":{"loadouts":[]}} if payload is None else payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("synthetic request failed")
    def json(self):
        return self.payload

class FakeSession:
    def __init__(self, response):
        self.response=response
        self.trust_env=True
        self.calls=[]
        self.closed=False
    def __enter__(self):
        return self
    def __exit__(self,*_):
        self.closed=True
    def get(self,url,*,headers,allow_redirects,timeout):
        self.calls.append((url,headers,allow_redirects,timeout,self.trust_env))
        return self.response

def fake_session(monkeypatch, response=None):
    session=FakeSession(response if response is not None else FakeResponse())
    monkeypatch.setattr(t.requests,"Session",lambda:session)
    return session

def test_admin_get_uses_existing_header_without_proxies_or_redirects(monkeypatch):
    session=fake_session(monkeypatch)
    result=t.fetch_router_admin_ops_summary("http://router.example:8088",TOKEN)
    assert result["fleet_loadout_report"]["loadouts"]==[]
    assert session.calls==[(
        "http://router.example:8088/admin/ops/summary",
        {"Accept":"application/json","X-Admin-Token":TOKEN},
        False,6.0,False)]
    assert session.closed

@pytest.mark.parametrize("value",["",None,"   ",False])
def test_missing_token_never_starts_network(monkeypatch,value):
    monkeypatch.setattr(t.requests,"Session",lambda:(_ for _ in ()).throw(AssertionError("no call")))
    with pytest.raises(t.RouterAdminReadDenied,match="router_admin_token_missing"):
        t.fetch_router_admin_ops_summary("http://router.example:8088",value)

@pytest.mark.parametrize("url",[
    "http://username:secret@router.example:8088",
    "http://router.example:8088?redirect=1",
    "http://router.example:8088/path",
    "http://router.example:8088/#fragment",
    "file:///etc/passwd",
    "not-a-url",
    "http://",
    "http://[invalid-ipv6",
])
def test_invalid_admin_base_never_sends_header(monkeypatch,url):
    monkeypatch.setattr(t.requests,"Session",lambda:(_ for _ in ()).throw(AssertionError("no call")))
    with pytest.raises(t.RouterAdminReadDenied,match="router_admin_base_url_invalid"):
        t.fetch_router_admin_ops_summary(url,TOKEN)

@pytest.mark.parametrize("status", [301,302,303,307,308])
def test_redirects_denied_without_followup(monkeypatch,status):
    session=fake_session(monkeypatch,FakeResponse(status=status))
    with pytest.raises(t.RouterAdminReadDenied,match="router_admin_redirect_denied"):
        t.fetch_router_admin_ops_summary("https://router.example",TOKEN)
    assert len(session.calls)==1
    assert session.calls[0][2] is False
    assert session.closed

@pytest.mark.parametrize("status", [401,403])
def test_bad_token_fails_closed(monkeypatch,status):
    session=fake_session(monkeypatch,FakeResponse(status=status))
    with pytest.raises(t.RouterAdminReadDenied,match="router_admin_auth_denied"):
        t.fetch_router_admin_ops_summary("http://router.example",TOKEN)
    assert session.closed

def test_http_error_does_not_echo_credential(monkeypatch):
    class LeakyResponse(FakeResponse):
        def raise_for_status(self):
            raise requests.HTTPError("hidden-secret-is-not-for-logs")
    fake_session(monkeypatch,LeakyResponse(500))
    with pytest.raises(t.RouterAdminReadDenied) as exc:
        t.fetch_router_admin_ops_summary("http://router.example",TOKEN)
    assert str(exc.value)=="router_admin_read_unavailable"
    assert TOKEN not in str(exc.value)
    assert "hidden-secret" not in str(exc.value)

def test_bad_json_shape_is_denied(monkeypatch):
    fake_session(monkeypatch,FakeResponse(payload=["not-a-dict"]))
    with pytest.raises(t.RouterAdminReadDenied,match="router_admin_response_invalid"):
        t.fetch_router_admin_ops_summary("http://router.example",TOKEN)


def test_fleet_report_source_passes_admin_token_through_safe_helper():
    """Ensure the legacy API cannot regress to the unauthenticated GET helper."""
    import ast
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "src" / "assistx" / "api.py"
    tree = ast.parse(source.read_text())
    functions = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}
    report = functions["_fetch_auto_router_fleet_report"]
    calls = [n for n in ast.walk(report) if isinstance(n, ast.Call)]
    safe_calls = [n for n in calls if isinstance(n.func, ast.Name)
                  and n.func.id == "fetch_router_admin_ops_summary"]
    assert len(safe_calls) == 1
    assert len(safe_calls[0].args) == 2
    assert all(isinstance(arg, ast.Name) for arg in safe_calls[0].args)
    assert [arg.id for arg in safe_calls[0].args] == ["base_url", "admin_token"]
    assert not any(isinstance(n.func, ast.Name) and n.func.id == "_fetch_json"
                   for n in calls)


def test_public_fleet_reads_are_not_forced_through_admin_transport():
    import ast
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "src" / "assistx" / "api.py"
    tree = ast.parse(source.read_text())
    functions = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}
    assert "_fetch_json" in functions
    helper = functions["_fetch_json"]
    calls = [n for n in ast.walk(helper) if isinstance(n, ast.Call)]
    assert any(isinstance(n.func, ast.Attribute) and n.func.attr == "get" for n in calls)
    assert not any(isinstance(n.func, ast.Name) and
                   n.func.id == "fetch_router_admin_ops_summary" for n in calls)
