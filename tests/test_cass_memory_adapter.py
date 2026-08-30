import pytest

from assistx.cass_memory_adapter import parse_cass_context


def test_cass_context_import_preserves_feedback_and_provenance():
    evidence = parse_cass_context(
        {
            "task": "fix tests",
            "relevantBullets": [
                {
                    "id": "b1",
                    "category": "workflow",
                    "content": "Run targeted tests before broad integration tests.",
                    "scope": "workspace",
                    "maturity": "established",
                    "helpfulCount": 8,
                    "harmfulCount": 1,
                    "sourceSessions": ["session-a", "session-b"],
                    "sourceAgents": ["claude", "codex"],
                    "effectiveScore": 0.82,
                    "reasoning": "Repeatedly reduced wasted integration cycles.",
                }
            ],
            "antiPatterns": [
                {
                    "id": "b2",
                    "category": "safety",
                    "content": "Do not delete state to fix a migration failure.",
                    "helpfulCount": 5,
                    "harmfulCount": 0,
                    "type": "anti-pattern",
                }
            ],
        }
    )
    assert len(evidence) == 2
    assert evidence[0].bullet_id == "b1"
    assert evidence[0].support == 9
    assert evidence[0].observed_success_rate == 8 / 9
    assert evidence[0].source_sessions == ("session-a", "session-b")
    assert evidence[0].effective_score == 0.82
    assert evidence[0].negative is False
    assert evidence[1].negative is True


def test_cass_context_rejects_negative_feedback_counts():
    with pytest.raises(ValueError, match="non-negative"):
        parse_cass_context(
            {
                "relevantBullets": [
                    {
                        "id": "bad",
                        "content": "bad",
                        "helpfulCount": -1,
                    }
                ]
            }
        )


def test_cass_context_skips_empty_content_but_requires_ids_for_real_bullets():
    assert parse_cass_context({"relevantBullets": [{"id": "empty", "content": ""}]}) == ()
    with pytest.raises(ValueError, match="id"):
        parse_cass_context({"relevantBullets": [{"content": "real rule"}]})
