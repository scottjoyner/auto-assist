#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import pathlib
import sys

_ASSISTX_RECEIPT_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "assistx"
    / "system_one_receipt.py"
)
_ASSISTX_SPEC = importlib.util.spec_from_file_location(
    "_assistx_system_one_receipt_contract",
    _ASSISTX_RECEIPT_PATH,
)
if _ASSISTX_SPEC is None or _ASSISTX_SPEC.loader is None:
    raise RuntimeError("could not load AssistX receipt module")
_assistx_receipt = importlib.util.module_from_spec(_ASSISTX_SPEC)
sys.modules[_ASSISTX_SPEC.name] = _assistx_receipt
_ASSISTX_SPEC.loader.exec_module(_assistx_receipt)

ASSISTX_RECEIPT_SCHEMA = _assistx_receipt.RECEIPT_SCHEMA
assistx_canonical_json_bytes = _assistx_receipt.canonical_json_bytes
assistx_sha256_json = _assistx_receipt.sha256_json
validate_decision_receipt = _assistx_receipt.validate_decision_receipt
_producer_receipt = importlib.import_module("my_jev.decision_receipt")
PRODUCER_RECEIPT_SCHEMA = _producer_receipt.RECEIPT_SCHEMA
ProducerDecisionReceipt = _producer_receipt.DecisionReceipt
producer_receipt_bytes = _producer_receipt.deterministic_receipt_bytes

CONTRACT_SCHEMA = "assistx-my-jev-system-one-receipt-contract-v1"
_AUTHORITY_FIELDS = (
    "dispatch_allowed",
    "approval_granted",
    "claim_acquired",
    "mutation_allowed",
    "routing_authority_changed",
)


def sample_receipt() -> dict:
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
        "response_sha256": assistx_sha256_json(questions),
        "provider": {
            "provider_id": "my-jev",
            "provider_version": "assistx-agent-policy-v1",
            "model_id": "contract-fixture",
            "model_version": "fixture-v1",
            "model_artifact_sha256": "4" * 64,
            "runtime": {
                "device": "cpu",
                "temperature": 1.0,
            },
        },
        "latency_ms": 1.0,
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


def verify(producer_sha: str) -> dict:
    if ASSISTX_RECEIPT_SCHEMA != PRODUCER_RECEIPT_SCHEMA:
        raise ValueError("receipt schema constants diverged")

    payload = sample_receipt()
    consumer = validate_decision_receipt(payload)
    producer = ProducerDecisionReceipt.model_validate(payload)

    consumer_dump = consumer.model_dump(mode="json")
    producer_dump = producer.model_dump(mode="json")
    if consumer_dump != producer_dump:
        raise ValueError("producer and consumer normalized receipt models diverged")

    assistx_bytes = assistx_canonical_json_bytes(consumer_dump)
    producer_bytes = producer_receipt_bytes(producer)
    if assistx_bytes != producer_bytes:
        raise ValueError("producer and consumer canonical receipt bytes diverged")

    authority = consumer_dump["authority"]
    if tuple(authority) != _AUTHORITY_FIELDS:
        raise ValueError("authority field set or order diverged")
    if any(authority[name] is not False for name in _AUTHORITY_FIELDS):
        raise ValueError("authority contract widened")
    if consumer_dump["evidence_only"] is not True:
        raise ValueError("receipt ceased to be evidence-only")

    return {
        "schema": CONTRACT_SCHEMA,
        "status": "pass",
        "producer_repository": "scottjoyner/my-jev",
        "producer_sha": producer_sha,
        "receipt_schema": ASSISTX_RECEIPT_SCHEMA,
        "canonical_receipt_sha256": assistx_sha256_json(consumer_dump),
        "provider_id": consumer_dump["provider"]["provider_id"],
        "evidence_only": True,
        "authority": authority,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the pinned my-jev receipt contract against the "
            "AssistX receipt consumer without running inference."
        )
    )
    parser.add_argument("--producer-sha", required=True)
    args = parser.parse_args()

    try:
        result = verify(args.producer_sha)
    except (TypeError, ValueError) as exc:
        print(
            f"SYSTEM_ONE_CROSS_REPO_CONTRACT: BLOCKED {exc}",
            flush=True,
        )
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    print("SYSTEM_ONE_CROSS_REPO_CONTRACT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
