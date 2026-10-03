from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RECEIPT_SCHEMA = "system-one-decision-receipt-v1"
SHADOW_BINDING_SCHEMA = "assistx-system-one-shadow-receipt-evidence-v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecisionAuthority(_ExactModel):
    dispatch_allowed: Literal[False] = False
    approval_granted: Literal[False] = False
    claim_acquired: Literal[False] = False
    mutation_allowed: Literal[False] = False
    routing_authority_changed: Literal[False] = False


class DecisionProvider(_ExactModel):
    provider_id: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=512)
    model_version: str | None = Field(default=None, max_length=256)
    model_artifact_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    runtime: dict[str, Any] = Field(default_factory=dict)


class DecisionQuestionResult(_ExactModel):
    name: str = Field(min_length=1, max_length=256)
    type: Literal["noul", "choice", "score"]
    options: list[str] = Field(min_length=2, max_length=255)
    probabilities: list[float] = Field(min_length=2, max_length=255)
    choice: str = Field(min_length=1, max_length=4096)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    noul: float | None = Field(default=None, ge=0.0, le=1.0)
    score: float | None = None

    @field_validator("options")
    @classmethod
    def _validate_options(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("decision receipt options must be unique")
        if any(not option for option in value):
            raise ValueError("decision receipt options must be non-empty")
        return value

    @field_validator("probabilities")
    @classmethod
    def _validate_probabilities(cls, value: list[float]) -> list[float]:
        if any(
            not math.isfinite(item)
            or item < 0.0
            or item > 1.0
            for item in value
        ):
            raise ValueError(
                "probabilities must be finite values in [0, 1]"
            )
        if not math.isclose(
            sum(value),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-5,
        ):
            raise ValueError("probabilities must sum to 1")
        return value

    @model_validator(mode="after")
    def _validate_result(self) -> "DecisionQuestionResult":
        if len(self.options) != len(self.probabilities):
            raise ValueError(
                "option/probability cardinality mismatch"
            )
        if self.choice not in self.options:
            raise ValueError(
                "choice must be one of the supplied options"
            )

        selected = self.probabilities[
            self.options.index(self.choice)
        ]
        peak = max(self.probabilities)
        if not math.isclose(
            selected,
            peak,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise ValueError(
                "choice must correspond to a maximum-probability option"
            )
        if (
            self.confidence is not None
            and not math.isclose(
                self.confidence,
                selected,
                rel_tol=0.0,
                abs_tol=1e-5,
            )
        ):
            raise ValueError(
                "confidence must match selected option probability"
            )

        if self.type == "noul":
            if self.options != ["false", "true"]:
                raise ValueError(
                    "noul receipt options must be false/true"
                )
            if self.noul is None:
                raise ValueError(
                    "noul result requires the true probability"
                )
            if not math.isclose(
                self.noul,
                self.probabilities[1],
                rel_tol=0.0,
                abs_tol=1e-5,
            ):
                raise ValueError("noul must match P(true)")
            if self.score is not None:
                raise ValueError(
                    "noul result cannot carry a score"
                )
        elif self.type == "score":
            if self.score is None or not math.isfinite(self.score):
                raise ValueError(
                    "score result requires a finite score"
                )
            if self.noul is not None:
                raise ValueError(
                    "score result cannot carry noul"
                )
        elif self.noul is not None or self.score is not None:
            raise ValueError(
                "choice result cannot carry noul/score"
            )
        return self


class DecisionReceipt(_ExactModel):
    schema: Literal["system-one-decision-receipt-v1"] = RECEIPT_SCHEMA
    input_sha256: str = Field(pattern=_SHA256_PATTERN)
    question_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider: DecisionProvider
    latency_ms: float = Field(ge=0.0)
    questions: list[DecisionQuestionResult] = Field(min_length=1)
    resolver_result: str | None = Field(default=None, max_length=256)
    evidence_only: Literal[True] = True
    authority: DecisionAuthority = Field(
        default_factory=DecisionAuthority
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


def canonical_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value)
    ).hexdigest()


def validate_decision_receipt(
    value: Mapping[str, Any],
) -> DecisionReceipt:
    receipt = DecisionReceipt.model_validate(value)
    normalized_questions = [
        question.model_dump(mode="json")
        for question in receipt.questions
    ]
    expected_response_sha = sha256_json(
        normalized_questions
    )
    if receipt.response_sha256 != expected_response_sha:
        raise ValueError(
            "decision receipt response_sha256 mismatch"
        )
    return receipt


def bind_shadow_receipt(
    *,
    request_payload: Mapping[str, Any],
    receipt_value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and bind a System-One receipt to the AssistX shadow request.

    This binding is observation-only. It records the hash of the exact AssistX
    request alongside the model-visible hash emitted by the provider without
    claiming the two representations are byte-identical.
    """

    receipt = validate_decision_receipt(
        receipt_value
    )
    normalized_receipt = receipt.model_dump(
        mode="json"
    )
    return {
        "schema": SHADOW_BINDING_SCHEMA,
        "request_sha256": sha256_json(
            dict(request_payload)
        ),
        "decision_receipt_sha256": sha256_json(
            normalized_receipt
        ),
        "model_visible_input_sha256": (
            receipt.input_sha256
        ),
        "question_schema_sha256": (
            receipt.question_schema_sha256
        ),
        "candidate_set_sha256": (
            receipt.candidate_set_sha256
        ),
        "response_sha256": receipt.response_sha256,
        "provider": receipt.provider.model_dump(
            mode="json"
        ),
        "resolver_result": receipt.resolver_result,
        "evidence_only": True,
        "authoritative_behavior_changed": False,
        "authority": receipt.authority.model_dump(
            mode="json"
        ),
    }
