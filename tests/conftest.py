import os
import socket
import shutil
import subprocess
import time

import pytest
from assistx.neo4j_client import Neo4jClient

NEO4J_IMAGE = os.getenv("TEST_NEO4J_IMAGE", "neo4j:5.23.0")
NEO4J_USER = os.getenv("TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("TEST_NEO4J_PASSWORD", "livelongandprosper")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_neo4j(uri: str, timeout: int = 60) -> None:
    from neo4j import GraphDatabase

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            driver = GraphDatabase.driver(uri, auth=(NEO4J_USER, NEO4J_PASSWORD))
            with driver.session() as session:
                session.run("RETURN 1").single()
            driver.close()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"Neo4j did not become available at {uri} within {timeout}s")


@pytest.fixture(scope="session")
def neo4j_container():
    if shutil.which("docker") is None:
        pytest.skip("Docker is required to run ephemeral Neo4j tests")

    port = _find_free_port()
    container_name = f"assistx-test-neo4j-{int(time.time())}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "-d",
        "-p",
        f"{port}:7687",
        "-e",
        f"NEO4J_AUTH={NEO4J_USER}/{NEO4J_PASSWORD}",
        "-e",
        "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
        "--name",
        container_name,
        NEO4J_IMAGE,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    container_id = result.stdout.strip()
    uri = f"bolt://127.0.0.1:{port}"

    try:
        _wait_for_neo4j(uri)
        yield {"uri": uri, "user": NEO4J_USER, "password": NEO4J_PASSWORD}
    finally:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True)


@pytest.fixture(scope="session")
def neo4j_client(neo4j_container):
    client = Neo4jClient(
        uri=neo4j_container["uri"],
        user=neo4j_container["user"],
        password=neo4j_container["password"],
    )
    client.ensure_schema()
    yield client
    client.close()


@pytest.fixture
def seeded_neo4j(neo4j_client):
    with neo4j_client.driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    neo4j_client.ensure_schema()
    conversation_id = neo4j_client.upsert_conversation("pytest conversation", "pytest")
    neo4j_client.add_utterances(
        conversation_id,
        [
            {"id": "utterance-1", "text": "Hello from pytest", "author": "tester"},
        ],
    )
    neo4j_client.add_summary_and_tasks(
        conversation_id,
        {"text": "A short summary"},
        [{"title": "Review item", "status": "READY"}],
    )
    return neo4j_client
