"""Physical nonadmin graph-role negative evidence on disposable Neo4j ONLY.

Never connects to production; roles are created in the isolated ephemeral
neo4j_container fixture. This tests a role boundary, NOT global writer
enrollment or signed Task/admission authority.
"""
from __future__ import annotations

import secrets

import pytest
from neo4j import GraphDatabase, READ_ACCESS
from neo4j.exceptions import Neo4jError


@pytest.fixture(scope="module")
def isolated_graph(neo4j_container):
    uri=neo4j_container["uri"]
    admin=GraphDatabase.driver(
        uri,auth=(neo4j_container["user"],neo4j_container["password"]))
    role="assistx_observer_research"
    username="assistx_observer_research"
    password=secrets.token_urlsafe(32)
    try:
        with admin.session(database="neo4j") as session:
            session.run("""
                CREATE (:Task {id:'synthetic-task-rbac',
                       status:'CLAIMED',claim_id:'research-claim'})
            """).consume()
            session.run("""
                CREATE (:WorkSupplyState {name:'canonical',
                       allow_background:false,total_slots:0,free_slots:0})
            """).consume()
            session.run("""
                CREATE (:MemoryItem {id:'synthetic-private-memory',
                       content:'synthetic secret never output'})
            """).consume()
        with admin.session(database="system") as session:
            session.run(f"CREATE ROLE {role} IF NOT EXISTS").consume()
            session.run(f"GRANT ACCESS ON DATABASE neo4j TO {role}").consume()
            session.run(
                f"GRANT MATCH {{*}} ON GRAPH neo4j NODES Task, WorkSupplyState TO {role}"
            ).consume()
            session.run(
                f"CREATE USER {username} SET PASSWORD $password CHANGE NOT REQUIRED",
                {"password":password},
            ).consume()
            session.run(f"GRANT ROLE {role} TO {username}").consume()
        reader=GraphDatabase.driver(uri,auth=(username,password))
        try:
            yield admin,reader
        finally:
            reader.close()
    finally:
        admin.close()


def test_reader_sees_task_and_coordination_without_admin_privileges(isolated_graph):
    admin,reader=isolated_graph
    with reader.session(database="neo4j",default_access_mode=READ_ACCESS) as s:
        assert s.run("""
            MATCH (t:Task {id:'synthetic-task-rbac'})
            RETURN t.status AS status
        """).single()["status"]=="CLAIMED"
        assert s.run("""
            MATCH (w:WorkSupplyState {name:'canonical'})
            RETURN w.free_slots AS free
        """).single()["free"]==0
        # No MATCH privilege on unrelated, sensitive graph labels.
        assert s.run("""
            MATCH (m:MemoryItem)
            RETURN count(m) AS n
        """).single()["n"]==0
    with reader.session(database="system",default_access_mode=READ_ACCESS) as s:
        roles=s.run("SHOW CURRENT USER YIELD roles RETURN roles").single()["roles"]
        assert "admin" not in [x.lower() for x in roles]
    with admin.session(database="neo4j") as s:
        assert s.run("""
            MATCH (m:MemoryItem)
            RETURN count(m) AS n
        """).single()["n"]==1


@pytest.mark.parametrize("mutation",[
    "MATCH (t:Task {id:'synthetic-task-rbac'}) SET t.status='DONE'",
    "CREATE (:Task {id:'synthetic-forged-task'})",
    "MATCH (t:Task {id:'synthetic-task-rbac'}) DETACH DELETE t",
    "MATCH (w:WorkSupplyState {name:'canonical'}) "
        "SET w.free_slots=999,w.allow_background=true",
    "MERGE (:Task {id:'synthetic-merged-task'})",
])
def test_physical_write_denials_even_with_write_mode_driver(isolated_graph,mutation):
    admin,reader=isolated_graph
    with reader.session(database="neo4j") as s:
        with pytest.raises(Neo4jError) as err:
            s.run(mutation).consume()
    assert err.value.code.startswith("Neo.ClientError.Security.")
    with admin.session(database="neo4j") as s:
        task=s.run("""
            MATCH (t:Task {id:'synthetic-task-rbac'})
            RETURN t.status AS status
        """).single()
        assert task["status"]=="CLAIMED"
        gate=s.run("""
            MATCH (w:WorkSupplyState {name:'canonical'})
            RETURN w.free_slots AS free,w.allow_background AS bg
        """).single()
        assert gate["free"]==0 and gate["bg"] is False
        for badid in ("synthetic-forged-task","synthetic-merged-task"):
            assert s.run("""
                MATCH (t:Task {id:$id}) RETURN count(t) AS n
            """,{"id":badid}).single()["n"]==0


def test_readonly_graph_principal_cannot_grant_itself_privileges(isolated_graph):
    _admin,reader=isolated_graph
    with reader.session(database="system") as s:
        with pytest.raises(Neo4jError) as exc:
            s.run("""
                GRANT WRITE ON GRAPH neo4j TO assistx_observer_research
            """).consume()
    assert exc.value.code.startswith("Neo.ClientError.Security.")


def test_read_access_mode_is_not_a_role_boundary(isolated_graph):
    """Admin and observer still have different effective native privileges."""
    admin,reader=isolated_graph
    with admin.session(database="system",default_access_mode=READ_ACCESS) as s:
        r=s.run("SHOW CURRENT USER YIELD roles RETURN roles").single()
        assert any(x.lower()=="admin" for x in r["roles"])
    with reader.session(database="system",default_access_mode=READ_ACCESS) as s:
        r=s.run("SHOW CURRENT USER YIELD roles RETURN roles").single()
        assert not any(x.lower()=="admin" for x in r["roles"])
    # This test deliberately does not write through the admin client.
