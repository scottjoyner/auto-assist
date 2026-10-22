"""Read-only runtime AssistX owner/claim gate qualification tests."""
from __future__ import annotations

from pathlib import Path
import pytest
import hermes_live_api_owner_gate_preflight as gate


def sample(*,owner_flag=None, cmd=None, running=True, env=None):
    values = ["SAFE_UNRELATED_CONFIG=synthetic_only"]
    if owner_flag is not None:
        values.append("ASSISTX_REQUIRE_OWNER_BINDING="+owner_flag)
    values.extend(env or [])
    return {
        "Config":{
            "Cmd":cmd if cmd is not None else [
                "uvicorn","assistx.api_router:app","--host","0.0.0.0",
                "--port","8000"],
            "Env":values,
        },
        "State":{"Running":running},
    }


def routes():
    return set(gate.REQUIRED_ROUTES)


def test_deployed_owner_flag_absent_means_hold():
    result=gate.inspect_metadata(sample(),routes())
    assert result["decision"]=="HOLD"
    assert result["api_running"] is True
    assert result["api_router_entrypoint_verified"] is True
    assert result["claim_routes_registered"] is True
    assert result["owner_binding_enforced"] is False
    assert result["signed_owner_gateway_enabled"] is False
    assert result["signed_gateway_must_be_qualified_before_cutover"] is True
    assert result["production_deployment_authorized"] is False


def test_owner_flag_qualified_without_exposing_credential_values():
    response=gate.inspect_metadata(sample(
        owner_flag="true",env=["OPENAI_API_KEY=synthetic_should_never_print"]),routes())
    assert response["owner_binding_enforced"] is True
    assert response["signed_owner_gateway_enabled"] is False
    assert response["production_deployment_authorized"] is False
    assert response["credential_values_exposed"] is False
    assert "synthetic_should_never_print" not in str(response)


@pytest.mark.parametrize("flag",["", "false", "True", "yes", "1", "off"])
def test_owner_flag_must_match_exact_approved_true(flag):
    report=gate.inspect_metadata(sample(owner_flag=flag),routes())
    assert report["owner_binding_enforced"] is False


def test_wrong_api_entrypoint_held():
    report=gate.inspect_metadata(sample(cmd=[
        "uvicorn","assistx.api:app","--port","8000"]),routes())
    assert report["api_router_entrypoint_verified"] is False
    assert report["decision"]=="HOLD"


def test_stopped_api_held():
    report=gate.inspect_metadata(sample(running=False),routes())
    assert report["api_running"] is False
    assert report["decision"]=="HOLD"


def test_missing_claim_status_route_held():
    report=gate.inspect_metadata(sample(owner_flag="true"),routes() - {
        "/api/executor/claims/{task_id}/status"})
    assert report["claim_routes_registered"] is False
    assert report["production_deployment_authorized"] is False


@pytest.mark.parametrize("bad",[
    None,{},[], "bad", {"Config":{},"State":{"Running":True}},
    {"Config":{"Env":[]},"State":{}},
    {"Config":{"Env":["NO_EQUALS"],"Cmd":[]},"State":{"Running":True}},
    {"Config":{"Env":["X=1","X=2"],"Cmd":[]},"State":{"Running":True}},
])
def test_malformed_and_ambiguous_metadata_fail_closed(bad):
    with pytest.raises(gate.PreflightDenied):
        gate.inspect_metadata(bad,routes())


def test_default_is_inert_and_no_mutating_command_exists():
    source=Path(gate.__file__).read_text()
    assert 'parser.add_argument("--physical-readonly"' in source
    assert 'urllib.request.Request("http://127.0.0.1:8000/openapi.json"' in source
    assert '["docker","inspect","--format","{{json .}}",CONTAINER]' in source
    assert 'credential_values_exposed":False' in source
    for forbidden in (
        'docker run','docker exec','docker restart','docker stop',
        'docker compose','sshpass','/api/tasks/synthetic-task/claim',
        'method="POST"',
    ):
        assert forbidden not in source



def test_all_gateway_config_present_is_still_not_production_approval():
    report=gate.inspect_metadata(sample(owner_flag="true",env=[
        "ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY=true",
        "ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE=/app/attestation/owner.pub",
        "ASSISTX_OWNER_GATEWAY_NONCE_DB=/var/lib/assistx/owner-nonces.sqlite",
    ]),routes())
    assert report["signed_owner_gateway_enabled"]
    assert report["signed_gateway_key_path_configured"]
    assert report["signed_gateway_nonce_path_configured"]
    assert report["signed_gateway_must_be_qualified_before_cutover"] is False
    assert report["owner_trust_custody_independent"] is False
    assert report["production_deployment_authorized"] is False
