"""Offline tests for explicit bearer authorization on etcd JSON gateway.

No HTTP requests, client keys, Docker, SSH or production access.
"""
import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from trace_etcd_bearer_rbac_research import (
    EtcdBearerTLS, require_etcd_bearer
)
from trace_etcd_quorum_fence_research import FenceRefused


def client():
    obj = object.__new__(EtcdBearerTLS)
    obj.endpoint = "https://nonexistent.example.test:30000"
    obj.context = object()
    obj._token = "opaque-research-token"
    return obj


@pytest.mark.parametrize("token", [None, "", "  ", 17])
def test_never_construct_bearer_client_without_token(token):
    with patch("trace_etcd_bearer_rbac_research.EtcdTLS.__init__",
               return_value=None):
        with pytest.raises(ValueError, match="MISSING_RBAC"):
            EtcdBearerTLS("https://invalid", "ca", "cert", "key", token)


def test_http_json_gateway_request_explicitly_sets_authorization():
    obj = client()
    reply = MagicMock()
    reply.__enter__.return_value.read.return_value = b'{"header":{"cluster_id":"99"}}'
    with patch("trace_etcd_bearer_rbac_research.urlopen",
               return_value=reply) as mock_urlopen:
        result = obj.range("/assistx/research/fencing/approved")
    request = mock_urlopen.call_args.args[0]
    assert request.get_header("Authorization") == "opaque-research-token"
    assert request.get_header("Content-type") == "application/json"
    assert result["header"]["cluster_id"] == "99"
    assert request.get_method() == "POST"


@pytest.mark.parametrize("status", [401, 403])
def test_denied_bearer_fails_closed_without_anonymous_retry(status):
    obj = client()
    problem = HTTPError(obj.endpoint, status, "denied", None, io.BytesIO(b"{}"))
    with patch("trace_etcd_bearer_rbac_research.urlopen",
               side_effect=problem) as mock_urlopen:
        with pytest.raises(FenceRefused, match="ETCD_RBAC_ACCESS_DENIED"):
            obj.txn({"compare": [], "success": [], "failure": []})
    assert mock_urlopen.call_count == 1


def test_transport_outage_is_not_interpreted_as_free_capacity():
    obj = client()
    with patch("trace_etcd_bearer_rbac_research.urlopen",
               side_effect=URLError("disconnected")):
        with pytest.raises(FenceRefused, match="QUORUM_OPERATION_FAILED"):
            obj.range("/assistx/research/fencing/approved")


def test_bearer_secret_not_in_representation():
    obj = client()
    assert "opaque-research-token" not in repr(obj)
    assert "REDACTED" in repr(obj)
    with pytest.raises(FenceRefused, match="NO_ETCD_RBAC_TOKEN"):
        require_etcd_bearer("")


def test_physical_rbac_script_is_manual_and_exact_key_scoped():
    from pathlib import Path
    probe = Path(__file__).with_name(
        "probe_trace_etcd_rbac_physical.py").read_text()
    orchestrator = Path(__file__).with_name(
        "probe_trace_etcd_three_hosts.py").read_text()
    assert 'ASSISTX_RAFT_RBAC_RESEARCH' in orchestrator
    assert 'run_scoped_rbac(' in orchestrator
    assert '"/v3/auth/enable"' in probe
    assert '"/v3/auth/authenticate"' in probe
    assert '"permType": "READ"' in probe
    assert '"permType": "READWRITE"' in probe
    assert 'read_auth.admit(' in probe
    assert 'writer.range(base + "/active")' in probe
    assert 'writer_raw_kv_policy_bypass_still_possible' in probe
    assert '"production_authority": False' in probe
