import pytest

from assistx.evaluation.experiment_manifest import ExperimentManifest


def _manifest(**overrides):
    values = {
        "name": "context-compression",
        "variant": "stdlib-json-minify",
        "baseline_id": "raw-v1",
        "source_repository": "scottjoyner/auto-assist",
        "source_commit": "abc123",
        "corpus_id": "context-corpus-v1",
        "config": {"content_types": ["application/json", "tool/json"]},
    }
    values.update(overrides)
    return ExperimentManifest(**values)


def test_manifest_hash_is_stable_for_same_content():
    assert _manifest().manifest_id == _manifest().manifest_id


def test_manifest_hash_changes_when_variant_changes():
    assert _manifest().manifest_id != _manifest(variant="headroom-v1").manifest_id


def test_trace_metadata_contains_manifest_identity():
    manifest = _manifest(
        upstream_repository="headroomlabs-ai/headroom",
        upstream_version="deadbeef",
    )
    metadata = manifest.trace_metadata()
    assert metadata["manifest_id"] == manifest.manifest_id
    assert metadata["upstream"]["repository"] == "headroomlabs-ai/headroom"


def test_partial_upstream_pin_is_rejected():
    with pytest.raises(ValueError, match="supplied together"):
        _manifest(upstream_repository="owner/repo").validate()
