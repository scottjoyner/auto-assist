import pytest

from assistx.repository_source_binding import (
    RepositorySourceBinding,
    binding_to_document,
    document_to_binding,
)


# --- the provenance document is the transport, and it must survive a round trip


def make_serializable_binding(tmp_path) -> RepositorySourceBinding:
    return RepositorySourceBinding(
        repository="auto-ingest-swarm",
        repo_realpath=str(tmp_path / "auto-ingest-swarm"),
        worktree_realpath=str(tmp_path / "auto-ingest-swarm" / "wt"),
        branch="main",
        head_sha="a" * 40,
        task_id="t",
        work_id="w",
    )


def test_binding_round_trips_through_its_provenance_document(tmp_path):
    binding = make_serializable_binding(tmp_path)

    document = binding_to_document(binding)

    assert document_to_binding(document) == binding


def test_provenance_document_carries_the_head_the_report_compares_against(tmp_path):
    """The executor report reads head_sha straight off this dict.

    It is what makes a work packet falsifiable, so a rename in the model would
    silently turn source_binding_matches into a permanent None rather than
    failing here.
    """
    document = binding_to_document(make_serializable_binding(tmp_path))

    assert document["head_sha"] == "a" * 40


def test_provenance_document_adds_no_key_the_model_would_refuse(tmp_path):
    """The models set extra="forbid".

    An injected schema_version reads back as a validation error on every
    binding, so the document must be exactly the model's own fields.
    """
    document = binding_to_document(make_serializable_binding(tmp_path))
    fields = set(RepositorySourceBinding.model_fields)

    assert set(document) <= fields


def test_provenance_document_with_an_unknown_key_is_refused(tmp_path):
    """Pins why the serializer adds nothing."""
    document = binding_to_document(make_serializable_binding(tmp_path))
    document["schema_version"] = "assistx-repository-source-binding-v1"

    with pytest.raises(ValueError):
        document_to_binding(document)