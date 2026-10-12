"""SSO response replay contract: does not prove HTTPS identity."""
import hashlib
import json

import pytest

from assistx.fleet_sso_ingest import (
    sso_identity_consistent, tailnet_origin, untrusted_replay,
)

NOW = 1791654900.0


def identity(login="person@example.com"):
    return {
        "authenticated": True, "provider": "tailscale", "login": login,
        "account_id": "tailscale:" + hashlib.sha256(
            login.strip().lower().encode()).hexdigest()[:24],
    }


def witness():
    data = {
        "schema_version": 1,
        "authority": "observational_only_not_runtime_admission",
        "captured_at": "2026-10-10T17:54:30Z",
        "source_inventory_sha256": "c" * 64,
        "observations": [{
            "node": "PRIVATE_NODE", "state": "resident_verified",
            "models": [{"id": "prism-ml/bonsai-27b",
                        "state": "resident_verified", "model_kind": "chat_model"}],
        }],
    }
    return json.dumps(data, sort_keys=True).encode()

def public_projection(blob):
    return {
        "schema_version": 1, "source": "tailnet_observational_only",
        "state": "fresh", "authority": "not_admitted",
        "observed_nodes": 1, "observed_endpoints": 1,
        "resident_endpoints": 1, "truncated": False,
        "observation_sha256": hashlib.sha256(blob).hexdigest(),
        "source_inventory_sha256": "c" * 64,
        "models": [{
            "display_name": "bonsai-27b", "state": "resident_verified",
            "observed_instances": 1, "admitted": False, "selectable": False,
        }],
    }


def test_local_sso_contract_is_not_live_transport_authentication():
    blob = witness()
    result = untrusted_replay(
        "https://fleet.example.ts.net", identity(),
        public_projection(blob), blob, now=NOW,
    )
    assert result["status"] == "LOCAL_CONTRACT_PASS"
    assert result["identity_contract_pass"] is True
    assert result["source_binding_valid"] is True
    assert result["live_https_verified"] is False
    assert result["sso_transport_authenticated"] is False
    assert result["model_dispatches"] == 0
    assert "PRIVATE_NODE" not in json.dumps(result)
    assert "person@example.com" not in json.dumps(result)


@pytest.mark.parametrize("change", ["legacy", "anonymous", "bad_account", "wrong_login"])
def test_legacy_and_inconsistent_identity_claims_rejected(change):
    doc = identity()
    if change == "legacy":
        doc["provider"] = "legacy"
        doc["authenticated"] = False
    elif change == "anonymous":
        doc["authenticated"] = False
    elif change == "bad_account":
        doc["account_id"] = "tailscale:" + "a" * 24
    else:
        doc["login"] = "another@example.com"
    assert sso_identity_consistent(doc) is False
    blob = witness()
    output = untrusted_replay(
        "https://fleet.example.ts.net", doc,
        public_projection(blob), blob, now=NOW,
    )
    assert output["status"] == "REJECT"
    assert output["sso_transport_authenticated"] is False


def test_private_witness_mismatch_and_staleness_rejected():
    blob = witness()
    changed = json.loads(blob)
    changed["observations"][0]["node"] = "OTHER_NODE"
    modified = json.dumps(changed, sort_keys=True).encode()
    bad = untrusted_replay(
        "https://fleet.example.ts.net", identity(),
        public_projection(blob), modified, now=NOW,
    )
    assert bad["status"] == "REJECT"
    stale = untrusted_replay(
        "https://fleet.example.ts.net", identity(),
        public_projection(blob), blob, now=NOW + 10000,
    )
    assert stale["status"] == "REJECT"


@pytest.mark.parametrize("origin", [
    "http://fleet.example.ts.net",
    "https://127.0.0.1",
    "https://fleet.example.ts.net.attacker.invalid",
    "https://fleet.example.ts.net/path",
    "https://fleet.example.ts.net?token=value",
    "https://fleet.example.ts.net:8443",
])
def test_only_https_tailnet_root_accepted(origin):
    with pytest.raises(ValueError):
        tailnet_origin(origin)


def test_valid_tailnet_origin_is_canonical():
    assert tailnet_origin("https://FLEET.example.ts.net/") == "https://fleet.example.ts.net"
