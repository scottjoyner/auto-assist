"""Hermes mount cutover pure regression; no live Docker calls."""
import copy
import json
from pathlib import Path
import pytest
import hermes_mount_cutover_contract as guard

TARGETS = (
    "/app/hermes-home", "/app/src", "/root/knowledge",
    "/media/scott/NAS5", "/media/scott/SSD_4TB",
    "/root/.hermes/mcp", "/root/.ssh/config", "/root/.ssh/id_hermes",
    "/root/.ssh/id_hermes.pub", "/home/scott/git/auto-assign",
    "/home/scott/git/auto-assist", "/home/scott/git/auto-router",
    "/home/scott/git/hermes-agent", "/home/scott/git/lms",
    "/usr/local/bin/opencode",
    "/media/scott/SSD_4TB/hermes-home/.hermes/SOUL.md",
)
RW = set(TARGETS[:5]) | {
    "/home/scott/git/auto-assign", "/home/scott/git/auto-assist",
    "/home/scott/git/auto-router", "/home/scott/git/hermes-agent",
}

def inventory():
    return [{"Source": "/synthetic-private-source/" + str(i),
             "Destination": dest, "Type": "bind", "RW": dest in RW}
            for i,dest in enumerate(TARGETS)]

def plan(mounts):
    return guard.plan_from_inspect(mounts,guard.IMAGE,True)

def test_all_16_removed_before_two_narrow_replacements():
    report=plan(inventory())
    assert report["decision"]=="HOLD"
    assert report["remove_legacy_count"]==16
    assert report["live_writable_count"]==9
    assert {x["target"] for x in report["remove_legacy_mounts"]}==set(TARGETS)
    assert {x["target"] for x in report["replacement_mounts"]}=={
        "/workspace","/research/knowledge"}
    assert report["production_deployment_authorized"] is False

def test_host_source_paths_redacted_from_evidence():
    r=json.dumps(plan(inventory()))
    assert "/synthetic-private-source/" not in r
    assert "Source" not in r

@pytest.mark.parametrize("bad",[
    [],None,{}, "not-list", inventory()[:-1], inventory()+inventory(),
])
def test_bad_inventory_is_denied(bad):
    with pytest.raises(guard.CutoverDenied):
        plan(bad)

@pytest.mark.parametrize("mutated",[
    {"Destination":"/workspace"}, {"Destination":"/research/knowledge"},
    {"Destination":"relative"}, {"Destination":"/x/../y"},
    {"Source":"relative"}, {"Type":"volume"}, {"RW":"true"},
])
def test_unreviewed_mount_metadata_rejected(mutated):
    rows=inventory()
    rows[0].update(mutated)
    with pytest.raises(guard.CutoverDenied):
        plan(rows)

def test_duplicate_destinations_rejected():
    rows=inventory()
    rows[-1]["Destination"]=rows[0]["Destination"]
    with pytest.raises(guard.CutoverDenied):
        plan(rows)

def test_missing_sensitive_mount_rejected():
    rows=inventory()
    rows[7]["Destination"]="/not-the-known-ssh-key"
    with pytest.raises(guard.CutoverDenied):
        plan(rows)

@pytest.mark.parametrize("image, running", [
    ("latest",True),(guard.IMAGE,False),(None,True),
])
def test_exact_live_image_and_run_state_required(image,running):
    with pytest.raises(guard.CutoverDenied):
        guard.plan_from_inspect(inventory(),image,running)

def test_known_ssh_and_storage_and_mutable_repos_are_removed():
    r=plan(inventory())
    action={x["target"]:x["action"] for x in r["remove_legacy_mounts"]}
    assert action["/root/.ssh/id_hermes"]=="remove_private_ssh"
    assert action["/app/src"]=="remove_mutable_code"
    assert action["/root/knowledge"]=="remove_protected_storage"
    assert action["/media/scott/NAS5"]=="remove_unverified_nas_label"

def test_read_only_contract_never_authorizes_cutover():
    assert all(x.startswith(("live_","approved_","independent_",
                             "owner_","exact_")) for x in guard.BLOCKERS)
    assert "independent_witness_custody_missing" in guard.BLOCKERS
    assert guard.plan_from_inspect(inventory(),guard.IMAGE,True)["decision"]=="HOLD"

def test_no_mutating_docker_command_exists_in_source():
    s=Path(guard.__file__).read_text()
    assert '"{{json .Mounts}}"' in s and '"{{.Image}} {{.State.Running}}"' in s
    assert "Config.Env" not in s
    for forbidden in ('"docker", "run"', '"docker", "stop"',
                      '"docker", "restart"', '"docker", "exec"',
                      '"docker", "rm"', '"docker", "compose"'):
        assert forbidden not in s

def test_substituted_unreviewed_legacy_mount_denied():
    rows=inventory()
    rows[-1]["Destination"]="/var/run/docker.sock"
    with pytest.raises(guard.CutoverDenied):
        plan(rows)
