import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from probe_fleet_custody_node import probe


def test_timeout_is_incomplete(tmp_path):
    def timeout(*a,**kw):
        raise subprocess.TimeoutExpired("child",5)
    r=probe(tmp_path,"x1-370",5,runner=timeout)
    assert r=={"node":"x1-370","verdict":"INCOMPLETE","reason":"metadata_timeout"}


@pytest.mark.parametrize("status",["STALE","MANIFEST_CHECKED","QUARANTINED"])
def test_metadata_never_claims_custody(tmp_path,status):
    def run(*a,**kw):
        return subprocess.CompletedProcess(a,0,json.dumps({"node":"x1-370","status":status,"archive_hash_verified":True}),"")
    r=probe(tmp_path,"x1-370",5,runner=run)
    assert r["verdict"]=="METADATA_ONLY"
    assert not r["archive_hash_verified"] and not r["token_aggregation_qualified"]


def test_child_failure_or_corrupt_result(tmp_path):
    for rc,body in [(1,""),(0,"bad json"),(0,'{"node":"xwing","status":"STALE"}')]:
        r=probe(tmp_path,"x1-370",5,runner=lambda *a,**kw:subprocess.CompletedProcess(a,rc,body,""))
        assert r["verdict"]=="INCOMPLETE"


def test_outside_roster_refused(tmp_path):
    with pytest.raises(ValueError):
        probe(tmp_path,"../../private",5)
