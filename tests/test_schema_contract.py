from assistx.neo4j_client import Neo4jClient


class _RecordingSession:
    def __init__(self, statements):
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, *args, **kwargs):
        self.statements.append(query)


class _RecordingDriver:
    def __init__(self):
        self.statements = []
        self.databases = []

    def session(self, database=None):
        self.databases.append(database)
        return _RecordingSession(self.statements)


def test_ensure_schema_declares_migration_constraints_and_indexes():
    driver = _RecordingDriver()
    client = Neo4jClient.__new__(Neo4jClient)
    client.driver = driver
    client.database = "assistx_test"

    client.ensure_schema()

    schema = "\n".join(driver.statements)
    for label in (
        "Transcription",
        "Segment",
        "Task",
        "AgentRun",
        "ToolCall",
        "Artifact",
    ):
        assert any(
            f":{label})" in statement
            and "REQUIRE" in statement
            and ".id IS UNIQUE" in statement
            for statement in driver.statements
        )

    assert "FOR (t:Task)            ON (t.status)" in schema
    assert "FOR (t:Task)            ON (t.kind)" in schema
    assert "FOR (tr:Transcription)  ON (tr.key)" in schema
    assert driver.databases == ["assistx_test"]
