from assistx.pipeline.qa_pipeline import answer_question


def test_neo4j_fixture_can_seed_and_query(seeded_neo4j):
    rows = seeded_neo4j.get_ready_tasks()
    assert any(r.get("title") == "Review item" and r.get("status") == "READY" for r in rows)


def test_answer_question_with_mocked_llm_and_real_neo4j(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j

    def fake_execute_with_repairs(neo_arg, question, schema, max_attempts, log_cb):
        return ("RETURN 1 AS answer", [{"answer": 1}], [])

    monkeypatch.setattr("assistx.pipeline.qa_pipeline.execute_with_repairs", fake_execute_with_repairs)
    monkeypatch.setattr(
        "assistx.pipeline.qa_pipeline.generate_analysis_code",
        lambda question, rows: {"code": "result={'count': len(rows)}", "notes": "ok"},
    )
    monkeypatch.setattr(
        "assistx.pipeline.qa_pipeline.run_user_code",
        lambda code, rows: ({"count": len(rows)}, ""),
    )
    monkeypatch.setattr(
        "assistx.pipeline.qa_pipeline.chat",
        lambda messages, model=None: "Mock answer",
    )

    with neo.driver.session() as session:
        session.run(
            "MERGE (t:Task {id:$id}) "
            "ON CREATE SET t.status='READY', t.title='qa_ad_hoc', t.created_at_ts=timestamp(), t.created_at = datetime()",
            {"id": "qa_ad_hoc"},
        )

    out = answer_question(neo, "How many ready tasks?", model="test-model", log_to_neo=True)

    assert out["answer"] == "Mock answer"
    assert out["computed"] == {"count": 1}
    assert out["cypher"] == "RETURN 1 AS answer"
    assert out["run_id"] is not None
