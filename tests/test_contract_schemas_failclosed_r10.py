"""Fail-closed invariants of the shared ``assistx.contracts.schemas`` contracts (r10).

These seven Pydantic models are the cross-repo contracts auto-assist *owns* and
Sophia / auto-ingest / the fleet agents *consume*. They had no dedicated test
module: only ``repository_source_binding`` was referenced by any test. A drift
in an enum taxonomy, an ``extra`` policy, or a required field here is a
cross-repo break that only surfaces at a remote boundary, so pin them as
hermetic sentinels (pure models - no network, no DB, no service).

One of them, ``ArtifactPaths``, was in fact unusable: the module uses
``from __future__ import annotations`` but never imported ``Optional``, so
Pydantic left ``transcript_path``/``output_path``/``log_path`` as unresolved
forward references and *every* instantiation raised ``PydanticUserError``.
The source fix is the one-line ``from typing import Optional`` import; the
first four tests are the fail-before/pass-after regression for it.
"""

from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from assistx.contracts.schemas import (
    ArtifactPaths,
    AuthorityMode,
    AuthOutcome,
    AutoIngestMemoryEnrichment,
    EndpointStatus,
    EnrichmentKind,
    ModelEndpointRegistryEntry,
    NodeRegistryEntry,
    NodeStatus,
    RegisteredSpeaker,
    SpeakerStatus,
    TaskAuthority,
    VoiceAuthDecision,
)

# ---------------------------------------------------------------------------
# 1. ArtifactPaths regression (the genuine defect fixed this round)
# ---------------------------------------------------------------------------


def test_artifact_paths_is_instantiable_with_defaults():
    """Before the fix this raised PydanticUserError: `Optional` undefined."""
    obj = ArtifactPaths(task_id="task-1", base_dir="/srv/artifacts")
    assert obj.task_id == "task-1"
    assert obj.base_dir == "/srv/artifacts"
    assert obj.transcript_path is None
    assert obj.output_path is None
    assert obj.log_path is None
    assert obj.extra == {}


def test_artifact_paths_optional_fields_round_trip():
    obj = ArtifactPaths(
        task_id="task-2",
        base_dir="/srv/artifacts",
        transcript_path="/srv/artifacts/t.json",
        output_path="/srv/artifacts/out.json",
        log_path="/srv/artifacts/run.log",
        extra={"camera": "front"},
    )
    blob = obj.model_dump(mode="json")
    assert blob == {
        "task_id": "task-2",
        "base_dir": "/srv/artifacts",
        "transcript_path": "/srv/artifacts/t.json",
        "output_path": "/srv/artifacts/out.json",
        "log_path": "/srv/artifacts/run.log",
        "extra": {"camera": "front"},
    }
    assert ArtifactPaths.model_validate_json(json.dumps(blob)) == obj


def test_artifact_paths_rejects_unknown_field():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ArtifactPaths(task_id="t", base_dir="/tmp", surprise=True)


@pytest.mark.parametrize("missing", ["task_id", "base_dir"])
def test_artifact_paths_requires_identity_fields(missing):
    payload = {"task_id": "t", "base_dir": "/tmp"}
    payload.pop(missing)
    with pytest.raises(ValidationError, match="Field required"):
        ArtifactPaths(**payload)


# ---------------------------------------------------------------------------
# 2. Enum taxonomies are exact (adding/renaming a value is a contract change)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "enum_cls, expected",
    [
        (
            AuthOutcome,
            {
                "authenticated_scott",
                "unknown_speaker",
                "registered_user_unverified",
                "admin_voice_override",
                "rejected",
            },
        ),
        (SpeakerStatus, {"unknown", "registered", "verified", "rejected"}),
        (AuthorityMode, {"paperclip", "direct", "auto"}),
        (NodeStatus, {"up", "down", "draining", "unknown"}),
        (EndpointStatus, {"ready", "degraded", "down", "unknown"}),
        (
            EnrichmentKind,
            {
                "diarize",
                "summarize",
                "dashcam",
                "knowledge_vault",
                "transcript",
            },
        ),
    ],
    ids=["auth", "speaker", "authority", "node", "endpoint", "enrichment"],
)
def test_enum_taxonomy_is_exact(enum_cls, expected):
    assert {member.value for member in enum_cls} == expected


# ---------------------------------------------------------------------------
# 3. extra="forbid" on every contract model
# ---------------------------------------------------------------------------

VALID_PAYLOADS = [
    (VoiceAuthDecision, {"outcome": "rejected", "score": 0.0}),
    (TaskAuthority, {"task_id": "t-1", "mode": "direct"}),
    (RegisteredSpeaker, {"speaker_id": "spk-1"}),
    (NodeRegistryEntry, {"node_id": "node-1"}),
    (
        ModelEndpointRegistryEntry,
        {
            "endpoint_id": "ep-1",
            "model_name": "m",
            "base_url": "http://127.0.0.1:1234",
        },
    ),
    (AutoIngestMemoryEnrichment, {"enrichment_id": "enr-1", "kind": "transcript"}),
]


@pytest.mark.parametrize("model, payload", VALID_PAYLOADS, ids=[m.__name__ for m, _ in VALID_PAYLOADS])
def test_unknown_field_is_rejected(model, payload):
    assert model.model_config.get("extra") == "forbid"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model(**payload, unexpected_field="x")


# ---------------------------------------------------------------------------
# 4. VoiceAuthDecision numeric bounds (fail closed on out-of-range confidence)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [-0.01, 1.01, math.nan, math.inf, -math.inf],
    ids=["below", "above", "nan", "inf", "neg_inf"],
)
def test_voice_score_is_bounded_and_finite(bad):
    with pytest.raises(ValidationError):
        VoiceAuthDecision(outcome="authenticated_scott", score=bad)


@pytest.mark.parametrize("bad", [-0.1, 1.1, math.nan], ids=["below", "above", "nan"])
def test_voice_threshold_is_bounded(bad):
    with pytest.raises(ValidationError):
        VoiceAuthDecision(outcome="rejected", score=0.0, threshold=bad)


def test_voice_default_threshold_and_optional_fields():
    obj = VoiceAuthDecision(outcome="rejected", score=0.0)
    assert obj.threshold == 0.60
    assert obj.speaker_id is None
    assert obj.decided_at is None
    assert obj.reason is None


def test_voice_valid_boundaries_accepted():
    for score in (0.0, 1.0):
        obj = VoiceAuthDecision(outcome="rejected", score=score, threshold=score)
        assert obj.score == score
        assert obj.threshold == score


# ---------------------------------------------------------------------------
# 5. Enum fields reject values outside the taxonomy (no silent fallback)
# ---------------------------------------------------------------------------

ENUM_CASES = [
    (VoiceAuthDecision, {"score": 0.0}, "outcome"),
    (TaskAuthority, {"task_id": "t"}, "mode"),
    (RegisteredSpeaker, {"speaker_id": "s"}, "status"),
    (NodeRegistryEntry, {"node_id": "n"}, "status"),
    (
        ModelEndpointRegistryEntry,
        {
            "endpoint_id": "e",
            "model_name": "m",
            "base_url": "http://x",
        },
        "status",
    ),
    (AutoIngestMemoryEnrichment, {"enrichment_id": "x"}, "kind"),
]


@pytest.mark.parametrize(
    "model, payload, field",
    ENUM_CASES,
    ids=[m.__name__ for m, _, _ in ENUM_CASES],
)
def test_unknown_enum_value_is_rejected(model, payload, field):
    with pytest.raises(ValidationError):
        model(**payload, **{field: "not_a_real_value"})


# ---------------------------------------------------------------------------
# 6. Required identity fields and independent mutable defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model, payload, field",
    [
        (VoiceAuthDecision, {}, "outcome"),
        (TaskAuthority, {"mode": "direct"}, "task_id"),
        (TaskAuthority, {"task_id": "t"}, "mode"),
        (RegisteredSpeaker, {}, "speaker_id"),
        (NodeRegistryEntry, {}, "node_id"),
        (
            ModelEndpointRegistryEntry,
            {
                "model_name": "m",
                "base_url": "http://x",
            },
            "endpoint_id",
        ),
        (
            ModelEndpointRegistryEntry,
            {
                "endpoint_id": "e",
                "base_url": "http://x",
            },
            "model_name",
        ),
        (
            ModelEndpointRegistryEntry,
            {
                "endpoint_id": "e",
                "model_name": "m",
            },
            "base_url",
        ),
        (AutoIngestMemoryEnrichment, {"kind": "transcript"}, "enrichment_id"),
        (AutoIngestMemoryEnrichment, {"enrichment_id": "x"}, "kind"),
    ],
)
def test_required_field_is_enforced(model, payload, field):
    with pytest.raises(ValidationError, match="Field required"):
        model(**payload)


def test_safe_defaults_are_closed():
    speaker = RegisteredSpeaker(speaker_id="s")
    node = NodeRegistryEntry(node_id="n")
    endpoint = ModelEndpointRegistryEntry(
        endpoint_id="e",
        model_name="m",
        base_url="http://x",
    )
    authority = TaskAuthority(task_id="t", mode="direct")
    enrich = AutoIngestMemoryEnrichment(enrichment_id="x", kind="transcript")
    assert speaker.status is SpeakerStatus.UNKNOWN and speaker.is_me is False
    assert node.status is NodeStatus.UNKNOWN
    assert endpoint.status is EndpointStatus.UNKNOWN
    assert authority.required_capabilities == []
    assert enrich.metadata == {}


def test_mutable_defaults_are_not_shared_between_instances():
    a = RegisteredSpeaker(speaker_id="a")
    b = RegisteredSpeaker(speaker_id="b")
    a.metadata["leak"] = True
    assert b.metadata == {}

    c = NodeRegistryEntry(node_id="c")
    d = NodeRegistryEntry(node_id="d")
    c.capabilities.append("yolo")
    assert d.capabilities == []

    e = ArtifactPaths(task_id="e", base_dir="/tmp")
    f = ArtifactPaths(task_id="f", base_dir="/tmp")
    e.extra["leak"] = True
    assert f.extra == {}


def test_contract_models_round_trip_through_json():
    """A consumer reading JSON must get an equal contract object back."""
    models = [
        VoiceAuthDecision(outcome="admin_voice_override", score=0.91, speaker_id="spk-9", reason="override"),
        TaskAuthority(task_id="t-9", mode="paperclip", owner="node-1", required_capabilities=["shell"]),
        RegisteredSpeaker(
            speaker_id="spk-9", name="Scott", status=SpeakerStatus.VERIFIED, is_me=True, metadata={"source": "sophia"}
        ),
        NodeRegistryEntry(node_id="n-9", host="x1-370", status=NodeStatus.UP, capabilities=["yolo"]),
        ModelEndpointRegistryEntry(
            endpoint_id="e-9", model_name="m", base_url="http://x", status=EndpointStatus.DEGRADED, lane="fast"
        ),
        AutoIngestMemoryEnrichment(enrichment_id="enr-9", kind=EnrichmentKind.DASHCAM, summary="front camera"),
        ArtifactPaths(task_id="t-9", base_dir="/srv/a", transcript_path="/srv/a/t.json"),
    ]
    for obj in models:
        blob = json.dumps(obj.model_dump(mode="json"))
        assert type(obj).model_validate_json(blob) == obj
