"""Round-8 drift guard: canonical receipt serialization / digest invariants.

``validate_decision_receipt`` and ``bind_shadow_receipt`` pin a SHA-256 digest
of the *canonical* JSON form of a decision receipt. If ``canonical_json_bytes``
silently changes (key sorting, whitespace, unicode escaping, trailing newline,
NaN policy), every digest changes and previously bound receipts stop verifying.
The existing ``test_system_one_receipt.py`` uses ``sha256_json`` as a helper but
never pins the canonical bytes/digests themselves.

All tests here are pure-function / in-memory: no network, no live DB, no
service. They complement (do not duplicate) R6's generation-fence tests.
"""

from __future__ import annotations

import hashlib

import pytest

from assistx.system_one_receipt import (
    DecisionAuthority,
    bind_shadow_receipt,
    canonical_json_bytes,
    sha256_json,
    validate_decision_receipt,
)

# Byte-exact golden values recorded from the canonical implementation on
# 2026-11-26. A change to any of these is a deliberate serialization change
# that must be reviewed (all previously issued receipt digests would move).
_GOLDEN = [
    (
        "simple",
        {"b": 1, "a": 2},
        b'{"a":2,"b":1}\n',
        "81103aa69250ea56e887eaab3cd9bf363d341563f05d0676be389c3e40a72871",
    ),
    (
        "unicode",
        {"k": "caf\u00e9/\u65e5\u672c"},
        b'{"k":"caf\xc3\xa9/\xe6\x97\xa5\xe6\x9c\xac"}\n',
        "dc532395d6d413dd2a5f3e70e42d0e6e67a79feb4611b44c839044ad587ca5d3",
    ),
    (
        "nested",
        {"z": [3, 1, 2], "a": {"y": True, "x": None}},
        b'{"a":{"x":null,"y":true},"z":[3,1,2]}\n',
        "4a542f7f04d3a35d9c12ed93267a90c7e32d2aa0a00bd4643ef32341ca6f246e",
    ),
    (
        "floats",
        {"n": 1.5, "e": 0.0},
        b'{"e":0.0,"n":1.5}\n',
        "45f8dc9a66d5fdb97ed8829878aedea4765d7452ae00bf50171f525cc2a56786",
    ),
]


def _minimal_receipt() -> dict:
    """Smallest valid receipt — independent fixture for the digest guard."""
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
        },
        "latency_ms": 12.5,
        "questions": questions,
        "resolver_result": "chat",
        "evidence_only": True,
    }


# ---------------------------------------------------------------------------
# Canonical byte / digest golden pins
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "value", "expected_bytes", "expected_sha"), _GOLDEN)
def test_canonical_bytes_golden(name, value, expected_bytes, expected_sha):
    assert canonical_json_bytes(value) == expected_bytes


@pytest.mark.parametrize(("name", "value", "expected_bytes", "expected_sha"), _GOLDEN)
def test_canonical_digest_golden(name, value, expected_bytes, expected_sha):
    assert sha256_json(value) == expected_sha


def test_sha256_json_digests_the_canonical_bytes():
    for _name, value, _expected_bytes, _expected_sha in _GOLDEN:
        assert sha256_json(value) == hashlib.sha256(canonical_json_bytes(value)).hexdigest()


# ---------------------------------------------------------------------------
# Serialization rule invariants
# ---------------------------------------------------------------------------


def test_key_insertion_order_does_not_change_canonical_bytes():
    assert canonical_json_bytes({"b": 1, "a": 2}) == canonical_json_bytes({"a": 2, "b": 1})


def test_nested_key_insertion_order_does_not_change_digest():
    first = {"outer": {"z": 1, "a": 2}, "head": True}
    second = {"head": True, "outer": {"a": 2, "z": 1}}
    assert sha256_json(first) == sha256_json(second)


def test_canonical_form_uses_compact_separators_and_one_trailing_newline():
    raw = canonical_json_bytes({"a": 1, "b": [2, 3]})
    assert b", " not in raw
    assert b": " not in raw
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    assert raw.count(b"\n") == 1


def test_canonical_form_does_not_escape_unicode():
    raw = canonical_json_bytes({"k": "caf\u00e9"})
    assert "\u00e9".encode("utf-8") in raw
    assert b"\\u00e9" not in raw


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_fail_closed(bad):
    with pytest.raises(ValueError):
        canonical_json_bytes({"n": bad})


def test_pydantic_model_serializes_as_its_json_dump():
    model = DecisionAuthority()
    dumped = model.model_dump(mode="json")
    assert canonical_json_bytes(model) == canonical_json_bytes(dumped)
    assert sha256_json(model) == sha256_json(dumped)


def test_authority_model_digest_golden():
    # Authority is a Literal[False] model — a single flipped default would
    # change every shadow-receipt binding digest.
    assert sha256_json(DecisionAuthority()) == ("ecc991a225d449000418a92e7e5ab014b5727c7015f6f3461908aec8fbeed336")


# ---------------------------------------------------------------------------
# Bound-receipt digest stability
# ---------------------------------------------------------------------------


def test_bound_receipt_digest_golden():
    receipt = _minimal_receipt()
    validate_decision_receipt(receipt)  # guard: fixture stays valid
    bound = bind_shadow_receipt(request_payload={"state": {"utterance": "hi"}}, receipt_value=receipt)
    assert bound["decision_receipt_sha256"] == ("375254573b350ee93eac0336da7d922f823cbe2114a6b93bdb676f17216a1c39")
    assert bound["request_sha256"] == ("84d00daf39184a5d4e71938bfdbe424e67b2e8d223f1d898783e00aadd01f779")


def test_bound_receipt_digest_is_independent_of_key_order():
    receipt = _minimal_receipt()
    reordered = {key: receipt[key] for key in reversed(list(receipt.keys()))}
    reordered["provider"] = {key: receipt["provider"][key] for key in reversed(list(receipt["provider"].keys()))}
    first = bind_shadow_receipt(request_payload={"a": 1, "b": 2}, receipt_value=receipt)
    second = bind_shadow_receipt(request_payload={"b": 2, "a": 1}, receipt_value=reordered)
    assert first["decision_receipt_sha256"] == second["decision_receipt_sha256"]
    assert first["request_sha256"] == second["request_sha256"]


def test_bound_receipt_digest_changes_on_field_tamper():
    original = _minimal_receipt()
    tampered = dict(original)
    tampered["latency_ms"] = 99.0
    first = bind_shadow_receipt(request_payload={"a": 1}, receipt_value=original)
    second = bind_shadow_receipt(request_payload={"a": 1}, receipt_value=tampered)
    assert first["decision_receipt_sha256"] != second["decision_receipt_sha256"]


def test_binding_preserves_provider_identity_hashes():
    receipt = _minimal_receipt()
    bound = bind_shadow_receipt(request_payload={"x": 1}, receipt_value=receipt)
    assert bound["model_visible_input_sha256"] == receipt["input_sha256"]
    assert bound["question_schema_sha256"] == receipt["question_schema_sha256"]
    assert bound["candidate_set_sha256"] == receipt["candidate_set_sha256"]
    assert bound["response_sha256"] == receipt["response_sha256"]
