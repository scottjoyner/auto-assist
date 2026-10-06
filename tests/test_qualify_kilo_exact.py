import importlib.util
import pathlib

MODULE_PATH = pathlib.Path(__file__).parent.parent / "scripts" / "qualify_kilo_exact.py"
SPEC = importlib.util.spec_from_file_location("qualify_kilo_exact", MODULE_PATH)
qualifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qualifier)


def test_validate_candidate_requires_live_zero_cost_exact_tool_model():
    model = "vendor/code-model:free"
    catalog = {
        model: {
            "id": model,
            "pricing": {"prompt": "0", "completion": "0"},
            "supported_parameters": ["tools", "tool_choice"],
        }
    }
    assert qualifier.validate_candidate(model, catalog)["id"] == model


def test_alias_and_non_tool_candidate_fail_closed():
    try:
        qualifier.validate_candidate("openrouter/free", {})
        raise AssertionError("alias should fail")
    except ValueError:
        pass

    model = "vendor/media:free"
    try:
        qualifier.validate_candidate(
            model,
            {model: {"id": model, "supported_parameters": ["temperature"]}},
        )
        raise AssertionError("non-tool model should fail")
    except ValueError:
        pass


def test_success_requires_exact_response_model_structured_tool_and_zero_cost():
    model = "vendor/code:free"
    response = {
        "model": model,
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "echo_value",
                                "arguments": '{"value":"QUALIFIED_OK"}',
                            },
                        }
                    ]
                },
            }
        ],
        "usage": {
            "cost": 0,
            "cost_details": {"upstream_inference_cost": 0},
        },
    }
    result = qualifier.evaluate_success(model, 200, response)
    assert result["qualified"] is True
    assert result["model_identity_complete"] is True
    assert result["structured_tool_call"] is True


def test_mismatched_response_model_is_not_qualified():
    result = qualifier.evaluate_success(
        "vendor/requested:free",
        200,
        {
            "model": "vendor/other:free",
            "choices": [{"message": {"tool_calls": []}}],
            "usage": {"cost": 0},
        },
    )
    assert result["qualified"] is False
    assert result["model_identity_complete"] is False


def test_failure_state_is_sanitized():
    state, metadata = qualifier._failure_state(
        429,
        {
            "error": {
                "code": 429,
                "metadata": {
                    "provider_name": "Example",
                    "limit_source": "shared_pool",
                    "is_byok": False,
                    "raw": "secret-ish upstream body",
                },
            }
        },
    )
    assert state == "rate_limited"
    assert metadata == {
        "provider_name": "Example",
        "limit_source": "shared_pool",
        "is_byok": False,
        "provider_code": 429,
    }


def test_private_ledger_append(tmp_path):
    root = tmp_path / "state"
    ledger = root / "provider-qualification.jsonl"
    qualifier.append_record(ledger, {"requested_model": "x", "qualified": True})
    assert ledger.exists()
    assert ledger.stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700
    assert "requested_model" in ledger.read_text()
