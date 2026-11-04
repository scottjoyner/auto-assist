"""Fail-closed regression tests for actual Neo4j 5.x backup inspector tables."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from assistx.neo4j_backup_verification import (
    BackupVerificationError,
    Neo4jBackupVerifier,
)

ID_A = "692432fb-d218-407d-839d-e361716afe75"
ID_B = "ce495baf-12da-4dba-9016-64b783ac6d97"


def row(
    db: str = "assistx",
    *,
    time: str = "2026-11-03T08:20:31",
    full: str = "true",
    compressed: str = "true",
    low: str = "1",
    high: str = "15112223",
    database_id: str | None = None,
    filename_time: str = "2026-11-03T08-20-31",
) -> str:
    uid = database_id or (ID_A if db == "assistx" else ID_B)
    return (
        f"| file:///backups/{db}-{filename_time}.backup | {db} | {uid} "
        f"| {time} | {full} | {compressed} | {low} | {high} |"
    )


HEADER = "| FILE | DATABASE | DATABASE ID | TIME (UTC) | FULL | COMPRESSED | LOWEST TX | HIGHEST TX |"


def _verify_table(table: str, db: str = "assistx", *, clock_hours_after: int = 18):
    timestamp=datetime(2026,11,3,8,20,31,tzinfo=UTC)
    now=timestamp+timedelta(hours=clock_hours_after)
    def runner(cmd, **_kwargs):
        if cmd[1:3] == ["backup","inspect"]:
            return SimpleNamespace(returncode=1,stdout="",stderr="unsupported")
        return SimpleNamespace(returncode=0,stdout=table,stderr="")
    return Neo4jBackupVerifier(
        runner, clock_ms=lambda: int(now.timestamp()*1000)
    ).verify("/backups",db,max_age_seconds=24*3600)


def test_filename_T_does_not_override_actual_time_column():
    data="\n".join((HEADER,row()))
    rows=Neo4jBackupVerifier._parse_output(data,"assistx")
    assert len(rows)==1
    want=datetime(2026,11,3,8,20,31,tzinfo=UTC)
    assert rows[0].timestamp_ms==int(want.timestamp()*1000)
    assert rows[0].timestamp_ms>1_700_000_000_000
    assert _verify_table(data)["ok"] is True


def test_filename_timestamp_disagrees_but_explicit_time_wins():
    data=row(filename_time="1970-01-01T00-00-00",time="2026-11-03T08:20:31")
    rows=Neo4jBackupVerifier._parse_output(data,"assistx")
    assert len(rows)==1
    assert rows[0].timestamp_ms==int(datetime(
        2026,11,3,8,20,31,tzinfo=UTC
    ).timestamp()*1000)


def test_compressed_differential_never_masquerades_as_full():
    data=row(full="false",compressed="true")
    a=Neo4jBackupVerifier._parse_output(data,"assistx")
    assert len(a)==1 and a[0].full is False
    with pytest.raises(BackupVerificationError,match="does not begin with a full"):
        _verify_table(data)


def test_full_true_compressed_false_is_still_full():
    data=row(full="true",compressed="false")
    assert Neo4jBackupVerifier._parse_output(data,"assistx")[0].full is True
    assert _verify_table(data)["ok"] is True


def test_differential_accepted_only_after_real_full_contiguous():
    data="\n".join([
        row(time="2026-11-02T08:20:31",filename_time="2026-11-02T08-20-31",high="100",full="true"),
        row(time="2026-11-03T08:20:31",filename_time="2026-11-03T08-20-31",
            low="101",high="120",full="false",compressed="true")
    ])
    result=_verify_table(data)
    assert result["artifact_count"]==2
    assert result["highest_tx"]==120


@pytest.mark.parametrize("bad_full",["maybe","","1","null","yes"])
def test_invalid_full_value_fails_closed(bad_full):
    with pytest.raises(BackupVerificationError,match="boolean"):
        Neo4jBackupVerifier._parse_output(row(full=bad_full),"assistx")


@pytest.mark.parametrize("bad_compressed",["maybe","","1","null"])
def test_invalid_compression_value_fails_closed(bad_compressed):
    with pytest.raises(BackupVerificationError,match="boolean"):
        Neo4jBackupVerifier._parse_output(row(compressed=bad_compressed),"assistx")


@pytest.mark.parametrize("bad_timestamp",["not-a-time","","0000-01-01T00:00:00","1970-not-date"])
def test_bad_time_column_rejected_even_if_filename_has_valid_timestamp(bad_timestamp):
    with pytest.raises(BackupVerificationError,match="timestamp"):
        Neo4jBackupVerifier._parse_output(row(time=bad_timestamp),"assistx")


@pytest.mark.parametrize("bad_uuid",["0123","00000000-0000-0000-0000-00000000000z",""])
def test_missing_or_corrupted_database_uuid_rejected(bad_uuid):
    line=row()
    original=ID_A
    changed=line.replace(original,bad_uuid)
    with pytest.raises(BackupVerificationError,match="identity"):
        Neo4jBackupVerifier._parse_output(changed,"assistx")


@pytest.mark.parametrize("low,high",[("-1","10"),("11","10"),("oops","15")])
def test_invalid_transaction_range_rejected(low,high):
    with pytest.raises(BackupVerificationError,match="transaction"):
        Neo4jBackupVerifier._parse_output(row(low=low,high=high),"assistx")


def test_target_database_column_must_match_exactly():
    x="\n".join([
        row(db="neo4j",database_id=ID_B),
        row(db="assistx",database_id=ID_A),
    ])
    a=Neo4jBackupVerifier._parse_output(x,"assistx")
    b=Neo4jBackupVerifier._parse_output(x,"neo4j")
    assert len(a)==len(b)==1
    assert a[0].database=="assistx" and b[0].database=="neo4j"


def test_malformed_target_row_fails_instead_of_shrinking_chain():
    bad=row()+" | extra-field |"
    with pytest.raises(BackupVerificationError,match="column count"):
        Neo4jBackupVerifier._parse_output("\n".join([row(),bad]),"assistx")


@pytest.mark.parametrize("value,expected",[
    ("true",True),("false",False),(True,True),(False,False)
])
def test_json_full_flags_do_not_use_string_truthiness(value,expected):
    payload=[{"path":"backup","database":"neo4j","databaseId":ID_B,
              "time":"2026-11-03T08:20:31",
              "full":value,"lowestTransactionId":1,"highestTransactionId":11,
              "recovered":value}]
    artifacts=Neo4jBackupVerifier._parse_output(json.dumps(payload),"neo4j")
    assert len(artifacts)==1
    assert artifacts[0].full is expected
    assert artifacts[0].recovered is expected


@pytest.mark.parametrize("bad",["yes","no",1,0,None,"",[],{}])
def test_json_unsupported_full_flag_fails_closed(bad):
    payload=[{"path":"backup","database":"neo4j","databaseId":ID_B,
              "time":"2026-11-03T08:20:31",
              "full":bad,"lowestTransactionId":1,"highestTransactionId":11}]
    with pytest.raises(BackupVerificationError,match="boolean"):
        Neo4jBackupVerifier._parse_output(json.dumps(payload),"neo4j")


def test_json_false_recovered_flag_does_not_pass_recovered_gate():
    payload=[{"path":"backup","database":"neo4j","databaseId":ID_B,
              "time":"2026-11-03T08:20:31","full":"true",
              "lowestTransactionId":1,"highestTransactionId":11,
              "recovered":"false"}]
    def runner(_cmd, **_kwargs):
        return SimpleNamespace(returncode=0,stdout=json.dumps(payload),stderr="")
    now=int((datetime(2026,11,3,9,0,tzinfo=UTC)).timestamp()*1000)
    v=Neo4jBackupVerifier(runner,clock_ms=lambda:now)
    with pytest.raises(BackupVerificationError,match="not recovered"):
        v.verify("/backups","neo4j",require_recovered=True)


def test_backup_artifact_output_no_raw_graph_data_or_passwords():
    data="\n".join((HEADER,row()))
    result=_verify_table(data)
    assert result["database"]=="assistx"
    assert result["highest_tx"]==15112223
    assert result["latest_age_seconds"]==18*3600
