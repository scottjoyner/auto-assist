#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from assistx.system_one_receipt import (
    RECEIPT_SCHEMA as ASSISTX_RECEIPT_SCHEMA,
    canonical_json_bytes as assistx_canonical_json_bytes,
    sha256_json as assistx_sha256_json,
    validate_decision_receipt,
)
from my_jev.decision_receipt import (
    RECEIPT_SCHEMA as PRODUCER_RECEIPT_SCHEMA,
    DecisionReceipt as ProducerDecisionReceipt,
    deterministic_receipt_bytes as producer_receipt_bytes,
)

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
