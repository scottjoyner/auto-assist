"""Hermetic regression tests for the ``file_exists`` acceptance fence.

These exercise the pure ``_file_exists`` predicate with synthetic ``tmp_path``
fixtures only. No live DB, docker, service, HTTP endpoint, credential file,
recovery-hold file, or network mount is touched.
"""

from __future__ import annotations


def _artifact():
    # Synthetic artifact record; its content is irrelevant to the predicate.
    return {"id": "art-1", "path": "/synthetic/artifact"}


def _predicate():
    # Imported lazily so the module import stays obvious and isolated.
    from assistx.acceptance import _file_exists

    return _file_exists


def test_missing_explicit_path_not_rescued_by_unrelated_artifact(tmp_path):
    """Fail-open regression: a named but absent path must not pass merely
    because the run produced some unrelated artifact."""
    missing = str(tmp_path / "required-output.txt")
    ok, info = _predicate()(
        {"id": "task-1"}, {"path": missing}, [_artifact()]
    )
    assert ok is False, (
        "file_exists accepted a missing required path on the strength of an "
        f"unrelated artifact (info={info!r})"
    )


def test_present_explicit_path_passes(tmp_path):
    present = tmp_path / "required-output.txt"
    present.write_text("ok", encoding="utf-8")
    ok, info = _predicate()(
        {"id": "task-1"}, {"path": str(present)}, [_artifact()]
    )
    assert ok is True
    assert "file exists" in info


def test_pathless_check_still_accepts_artifact_presence():
    """Preserve the fallback: with no path requested, any artifact qualifies."""
    ok, info = _predicate()({"id": "task-1"}, {}, [_artifact()])
    assert ok is True
    assert "artifact" in info


def test_no_path_and_no_artifact_fails(tmp_path):
    missing = str(tmp_path / "never-created.txt")
    assert _predicate()({"id": "task-1"}, {"path": missing}, [])[0] is False
    assert _predicate()({"id": "task-1"}, {}, [])[0] is False


def test_task_id_placeholder_expansion(tmp_path):
    """``{task_id}`` substitution must still resolve the concrete path."""
    target = tmp_path / "42.txt"
    target.write_text("ok", encoding="utf-8")
    templated = str(tmp_path / "{task_id}.txt")
    ok, _ = _predicate()({"id": "42"}, {"path": templated}, [])
    assert ok is True
