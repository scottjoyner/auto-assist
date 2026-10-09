from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistx import intent_orchestrator as io
from assistx import my_jev_policy
from assistx.system_one_receipt import (
    SHADOW_BINDING_SCHEMA,
    bind_shadow_receipt,
    sha256_json,
    validate_decision_receipt,
)


def receipt() -> dict:
    questions = [
        {
            "name": "route",
            "type": "choice",
            "options": ["chat", "act"],
            "probabilities": [0.9, 0.1],
            "choice": "chat",
            "confidence": 0.9,
            "noul": None,
            "score": None,
        }
    ]
    return {
        "schema": "system-one-decision-receipt-v1",
        "input_sha256": "1" * 64,
        "question_schema_sha256": "2" * 64,
        "candidate_set_sha256": "3" * 64,
        "response_sha256": sha256_json(questions),
        "provider": {
            "provider_id": "my-jev",
            "provider_version": "assistx-agent-policy-v1",
            "model_id": "checkpoint-fixture",
            "model_version": "fixture-v1",
            "model_artifact_sha256": "4" * 64,
            "runtime": {
                "device": "cpu",
                "temperature": 1.0,
            },
        },
        "latency_ms": 12.5,
        "questions": questions,
        "resolver_result": "chat",
        "evidence_only": True,
        "authority": {
            "dispatch_allowed": False,
            "approval_granted": False,
            "claim_acquired": False,
            "mutation_allowed": False,
            "routing_authority_changed": False,
        },
        "metadata": {
            "policy_contract": "assistx-agent-policy-v1",
        },
    }


def test_receipt_validation_is_strict_and_binds_request_identity():
    request = {
        "state": {"utterance": "hello"},
        "constraints": {"actions_allowed": False},
    }
    binding = bind_shadow_receipt(
        request_payload=request,
        receipt_value=receipt(),
    )

    assert binding["schema"] == SHADOW_BINDING_SCHEMA
    assert binding["provider"]["provider_id"] == "my-jev"
    assert binding["request_sha256"] == sha256_json(request)
    assert binding["model_visible_input_sha256"] == "1" * 64
    assert binding["authoritative_behavior_changed"] is False
    assert binding["authority"] == {
        "dispatch_allowed": False,
        "approval_granted": False,
        "claim_acquired": False,
        "mutation_allowed": False,
        "routing_authority_changed": False,
    }


def test_receipt_rejects_authority_widening_and_unknown_fields():
    widened = receipt()
    widened["authority"]["dispatch_allowed"] = True
    with pytest.raises(ValidationError):
        validate_decision_receipt(widened)

    extra = receipt()
    extra["authority"]["new_authority"] = False
    with pytest.raises(ValidationError):
        validate_decision_receipt(extra)


def test_receipt_rejects_response_hash_tamper():
    tampered = receipt()
    tampered["questions"][0]["probabilities"] = [0.8, 0.2]
    tampered["questions"][0]["confidence"] = 0.8
    with pytest.raises(ValueError, match="response_sha256"):
        validate_decision_receipt(tampered)


def test_shadow_policy_records_validated_receipt_evidence(monkeypatch):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "contract": "assistx-agent-policy-v1",
                "decision_receipt": receipt(),
                "resolved": {
                    "model_route": "chat",
                    "disposition": "chat",
                },
                "assistx": {
                    "policy_action": "answer_inline",
                },
            }

    def post(url, *, json, timeout):
        assert url
        assert timeout > 0
        assert json["state"]["utterance"] == "hello"
        return _Response()

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        post,
    )
    evidence = my_jev_policy.request_policy_shadow(
        {
            "id": "intent-receipt",
            "text": "hello",
            "classification": "query",
        }
    )

    assert evidence is not None
    bound = evidence["receipt_evidence"]
    assert bound["schema"] == SHADOW_BINDING_SCHEMA
    assert bound["provider"]["model_id"] == "checkpoint-fixture"
    assert bound["authoritative_behavior_changed"] is False


def test_shadow_policy_rejects_invalid_receipt_without_touching_live_decision(
    monkeypatch,
):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )
    bad = copy.deepcopy(receipt())
    bad["authority"]["mutation_allowed"] = True

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "contract": "assistx-agent-policy-v1",
                "decision_receipt": bad,
            }

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        lambda *args, **kwargs: _Response(),
    )
    with pytest.raises(ValidationError):
        my_jev_policy.request_policy_shadow(
            {
                "id": "intent-invalid-receipt",
                "text": "hello",
            }
        )


def test_receipt_requirement_is_opt_in(monkeypatch):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "MY_JEV_POLICY_REQUIRE_DECISION_RECEIPT",
        "true",
    )

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "contract": "assistx-agent-policy-v1",
            }

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        lambda *args, **kwargs: _Response(),
    )
    with pytest.raises(
        ValueError,
        match="missing required decision receipt",
    ):
        my_jev_policy.request_policy_shadow(
            {
                "id": "intent-missing-receipt",
                "text": "hello",
            }
        )


def test_missing_receipt_is_compatible_by_default(monkeypatch):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )
    monkeypatch.delenv(
        "MY_JEV_POLICY_REQUIRE_DECISION_RECEIPT",
        raising=False,
    )

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "contract": "assistx-agent-policy-v1",
            }

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        lambda *args, **kwargs: _Response(),
    )
    evidence = my_jev_policy.request_policy_shadow(
        {
            "id": "intent-legacy-no-receipt",
            "text": "hello",
        }
    )

    assert evidence is not None
    assert evidence["receipt_evidence"] is None


def test_non_object_receipt_fails_closed_inside_observer_job(
    monkeypatch,
):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "contract": "assistx-agent-policy-v1",
                "decision_receipt": "not-an-object",
            }

    class _Neo:
        def __init__(self):
            self.recorded = []

        def record_intent_policy_shadow(
            self,
            intent_id,
            evidence,
        ):
            self.recorded.append(
                (intent_id, evidence)
            )

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        lambda *args, **kwargs: _Response(),
    )
    neo = _Neo()
    io._record_my_jev_policy_shadow(
        neo,
        {
            "id": "intent-malformed-receipt",
            "text": "hello",
        },
    )

    assert neo.recorded == []


def test_offline_acceptance_harness_validates_expected_identity(
    tmp_path,
):
    request = {
        "state": {"utterance": "hello"},
        "constraints": {"actions_allowed": False},
    }
    response = {
        "contract": "assistx-agent-policy-v1",
        "decision_receipt": receipt(),
    }
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(
        json.dumps(request),
        encoding="utf-8",
    )
    response_path.write_text(
        json.dumps(response),
        encoding="utf-8",
    )

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate-system-one-shadow-receipt.py",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
            "--expected-provider-id",
            "my-jev",
            "--expected-model-id",
            "checkpoint-fixture",
            "--expected-model-artifact-sha256",
            "4" * 64,
            "--require-model-artifact",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    accepted = json.loads(result.stdout)
    assert accepted["status"] == "pass"
    assert accepted["provider"]["provider_id"] == "my-jev"
    assert accepted["authority"]["dispatch_allowed"] is False
    assert accepted["authoritative_behavior_changed"] is False


def test_offline_acceptance_harness_blocks_identity_mismatch(
    tmp_path,
):
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(
        json.dumps(
            {
                "state": {"utterance": "hello"},
                "constraints": {"actions_allowed": False},
            }
        ),
        encoding="utf-8",
    )
    response_path.write_text(
        json.dumps(
            {
                "contract": "assistx-agent-policy-v1",
                "decision_receipt": receipt(),
            }
        ),
        encoding="utf-8",
    )

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate-system-one-shadow-receipt.py",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
            "--expected-provider-id",
            "not-my-jev",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "provider_id does not match expected identity" in result.stderr


def test_shadow_policy_accepts_matching_configured_receipt_identity(
    monkeypatch,
):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "MY_JEV_POLICY_EXPECTED_PROVIDER_ID",
        "my-jev",
    )
    monkeypatch.setenv(
        "MY_JEV_POLICY_EXPECTED_MODEL_ID",
        "checkpoint-fixture",
    )
    monkeypatch.setenv(
        "MY_JEV_POLICY_EXPECTED_MODEL_ARTIFACT_SHA256",
        "4" * 64,
    )

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "contract": "assistx-agent-policy-v1",
                "decision_receipt": receipt(),
            }

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        lambda *args, **kwargs: _Response(),
    )
    evidence = my_jev_policy.request_policy_shadow(
        {
            "id": "intent-matching-identity",
            "text": "hello",
        }
    )

    assert evidence is not None
    assert (
        evidence["receipt_evidence"]["provider"]["provider_id"]
        == "my-jev"
    )


@pytest.mark.parametrize(
    ("env_name", "env_value", "message"),
    [
        (
            "MY_JEV_POLICY_EXPECTED_PROVIDER_ID",
            "other-provider",
            "provider_id",
        ),
        (
            "MY_JEV_POLICY_EXPECTED_MODEL_ID",
            "other-model",
            "model_id",
        ),
        (
            "MY_JEV_POLICY_EXPECTED_MODEL_ARTIFACT_SHA256",
            "5" * 64,
            "model_artifact_sha256",
        ),
    ],
)
def test_shadow_policy_rejects_configured_receipt_identity_mismatch(
    monkeypatch,
    env_name,
    env_value,
    message,
):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        env_name,
        env_value,
    )

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "contract": "assistx-agent-policy-v1",
                "decision_receipt": receipt(),
            }

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        lambda *args, **kwargs: _Response(),
    )
    with pytest.raises(
        ValueError,
        match=message,
    ):
        my_jev_policy.request_policy_shadow(
            {
                "id": "intent-identity-mismatch",
                "text": "hello",
            }
        )


def test_receipt_identity_mismatch_is_contained_inside_observer_job(
    monkeypatch,
):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "MY_JEV_POLICY_EXPECTED_PROVIDER_ID",
        "expected-provider",
    )

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "contract": "assistx-agent-policy-v1",
                "decision_receipt": receipt(),
            }

    class _Neo:
        def __init__(self):
            self.recorded = []

        def record_intent_policy_shadow(
            self,
            intent_id,
            evidence,
        ):
            self.recorded.append(
                (intent_id, evidence)
            )

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        lambda *args, **kwargs: _Response(),
    )
    neo = _Neo()
    io._record_my_jev_policy_shadow(
        neo,
        {
            "id": "intent-identity-observer",
            "text": "hello",
        },
    )

    assert neo.recorded == []


def test_shadow_observer_has_no_activity_without_opt_in(monkeypatch):
    monkeypatch.delenv("MY_JEV_POLICY_SHADOW_ENABLED", raising=False)
    monkeypatch.setattr(my_jev_policy, "request_policy_shadow",
        lambda _: (_ for _ in ()).throw(AssertionError("must not make network request")))
    class _NeverWrite:
        def record_intent_policy_shadow(self, *_):
            raise AssertionError("must not record")
    io._record_my_jev_policy_shadow(_NeverWrite(), {"id": "synthetic-absent"})


def test_shadow_observer_records_only_complete_validated_evidence(monkeypatch):
    monkeypatch.setenv("MY_JEV_POLICY_SHADOW_ENABLED", "true")
    observed = []
    class _Store:
        def record_intent_policy_shadow(self, intent_id, evidence):
            observed.append((intent_id, evidence))
    store = _Store()
    good = {"shadow": True, "receipt_evidence": {
        "schema": SHADOW_BINDING_SCHEMA, "authoritative_behavior_changed": False}}
    monkeypatch.setattr(my_jev_policy, "request_policy_shadow", lambda _: good)
    io._record_my_jev_policy_shadow(store, {"id": "synthetic-good"})
    assert observed == [("synthetic-good", good)]
    monkeypatch.setattr(my_jev_policy, "request_policy_shadow",
        lambda _: (_ for _ in ()).throw(ValueError("invalid receipt")))
    io._record_my_jev_policy_shadow(store, {"id": "synthetic-bad"})
    assert observed == [("synthetic-good", good)]
