"""Gate diagnostics are observational, never a replacement for signed admission."""
from assistx.runtime_admission_diagnostics import QUERIES, summarize


def fake_reader(*, expiry: int, eligible: int = 1, status: str = "approved"):
    calls = []
    def read(query, params):
        calls.append((query, params))
        if query == QUERIES["canonical"]:
            return [{"generation": 642, "revision": "foreign-generation",
                     "status": status, "expires_at_ts": expiry}]
        return [{"total": 5, "approved": 2, "eligible_now": eligible}]
    return read, calls


def test_expired_canonical_is_blocked_even_with_fresh_leaf_counts():
    read, _ = fake_reader(expiry=900, eligible=2)
    result = summarize(read, now_ms=2000)
    assert result["state"] == "blocked"
    assert result["canonical"]["expired_seconds"] == 1
    assert result["blockers"] == ["canonical_approval_missing_invalid_or_expired"]
    assert result["side_effects"] is False


def test_no_fresh_leaf_evidence_blocks_even_when_canonical_is_valid():
    read, _ = fake_reader(expiry=5000, eligible=0)
    result = summarize(read, now_ms=2000)
    assert result["state"] == "blocked"
    assert len(result["blockers"]) == 4
    assert "runtimes_no_current_approved_evidence" in result["blockers"]


def test_counts_are_only_candidates_even_when_everything_is_present():
    read, _ = fake_reader(expiry=5000, eligible=2)
    result = summarize(read, now_ms=2000)
    assert result["state"] == "candidate_evidence_present_not_verified"
    assert result["authority"] == "read_only_diagnostic_not_admission"
    assert result["blockers"] == []


def test_queries_are_bounded_readonly_and_parameterized():
    read, calls = fake_reader(expiry=2000)
    summarize(read, now_ms=2000)
    assert len(calls) == 5
    for query, params in calls:
        q = " ".join(query.upper().split())
        assert "MATCH " in q and "RETURN " in q
        assert all(token not in q for token in ("CREATE ", "MERGE ", "DELETE ", "SET ", "CALL ", "REMOVE "))
        assert params == {"now": 2000}


def test_missing_canonical_fails_closed():
    def read(query, params):
        return [] if query == QUERIES["canonical"] else [{"total": 0}]
    result = summarize(read, now_ms=2000)
    assert result["state"] == "blocked"
    assert result["canonical"]["valid"] is False
    assert len(result["blockers"]) == 5
