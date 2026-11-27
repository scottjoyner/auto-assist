"""Offline negative and provenance tests for read-only Hermes storage topology."""
from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import hermes_storage_topology_preflight as topology
import pytest


def snapshot():
    paths = {}
    for key, path in topology.EXPECTED_NODES.items():
        dev = 10 if key == "ssd" or key == "corpus_alias" else 5
        target = str(topology.SSD) if dev == 10 else "/"
        paths[key] = {
            "host_path": str(path),
            "resolved_path": (
                str(topology.SSD / "knowledge") if key == "corpus_alias"
                else str(path)),
            "is_symlink": key == "corpus_alias",
            "is_mountpoint": key == "ssd",
            "mount_target": target,
            "filesystem_type": "ext4",
            "device_id": dev,
            "directory_owner_uid": 1000,
            "directory_mode": 0o700,
            "available_bytes_now": 100 * 1024**3,
        }
    return paths


def result():
    return topology.assess_topology(snapshot())


def test_nas_label_does_not_automatically_mean_nas_is_mounted():
    r = result()
    assert r["decision"] == "HOLD"
    f = r["filesystem_topology_flags"]
    assert f["nas5_label_is_actual_mountpoint"] is False
    assert f["nas5_label_shares_local_root_device"] is True
    assert "nas5_label_is_not_a_mountpoint_on_this_host" in r["release_blockers"]
    assert "nas5_label_is_local_root_filesystem_not_verified_nas" in r["release_blockers"]
    assert r["nas5_availability_claimed"] is False


def test_scratch_is_not_operator_approved_just_because_private_and_roomy():
    r = result()
    f = r["filesystem_topology_flags"]
    assert f["scratch_owner_private"] and f["scratch_outside_protected_ssd"]
    assert f["scratch_has_20g_current_headroom"]
    assert r["scratch_capacity_reserved"] is False
    assert r["input_source_approved"] is False
    assert r["production_deployment_authorized"] is False


def test_protected_corpus_symlink_resolves_to_ssd():
    assert result()["filesystem_topology_flags"]["corpus_alias_resolves_inside_ssd"]


def test_scratch_and_mutable_git_share_local_device():
    assert result()["filesystem_topology_flags"]["scratch_and_git_share_device"]
    assert "scratch_shares_local_device_with_mutable_repositories" in result()["release_blockers"]


def test_distinct_separate_mounted_nas_still_requires_custody():
    paths = snapshot()
    nas = paths["nas5_label"]
    nas.update(is_mountpoint=True,
               mount_target=str(topology.NAS_LABEL), device_id=40)
    r = topology.assess_topology(paths)
    assert r["filesystem_topology_flags"]["nas5_label_is_actual_mountpoint"]
    assert not r["filesystem_topology_flags"]["nas5_label_shares_local_root_device"]
    assert r["decision"] == "HOLD"
    assert r["production_deployment_authorized"] is False


def test_missing_ssd_mount_blocks():
    paths = snapshot()
    paths["ssd"]["is_mountpoint"] = False
    r = topology.assess_topology(paths)
    assert "expected_ssd_mount_missing" in r["release_blockers"]


def test_wrong_corpus_alias_blocks():
    paths = snapshot()
    paths["corpus_alias"]["resolved_path"] = "/tmp/unrelated"
    r = topology.assess_topology(paths)
    assert "corpus_alias_changed_or_unverified" in r["release_blockers"]


def test_corpus_not_symlink_blocks_conflated_assumption():
    paths = snapshot()
    paths["corpus_alias"]["is_symlink"] = False
    assert not topology.assess_topology(paths)["filesystem_topology_flags"][
        "corpus_alias_resolves_inside_ssd"]


def test_private_scratch_on_protected_ssd_blocks():
    paths = snapshot()
    paths["scratch"]["resolved_path"] = str(topology.SSD / "scratch")
    paths["scratch"]["device_id"] = 10
    r = topology.assess_topology(paths)
    assert "scratch_is_not_separate_from_protected_ssd" in r["release_blockers"]


@pytest.mark.parametrize("owner,mode", [(0,0o700),(1000,0o755),(1001,0o700)])
def test_untrusted_scratch_owner_or_perms_rejected(owner, mode):
    paths=snapshot()
    paths["scratch"].update(directory_owner_uid=owner,directory_mode=mode)
    r=topology.assess_topology(paths)
    assert not r["filesystem_topology_flags"]["scratch_owner_private"]
    assert "scratch_parent_owner_or_permissions_unsafe" in r["release_blockers"]


@pytest.mark.parametrize("available", [0, 1, topology.MIN_CAPACITY_BYTES-1])
def test_low_disk_space_blocks(available):
    paths=snapshot()
    paths["scratch"]["available_bytes_now"]=available
    r=topology.assess_topology(paths)
    assert "scratch_current_space_below_minimum" in r["release_blockers"]


def test_threshold_is_inclusive():
    paths=snapshot()
    paths["scratch"]["available_bytes_now"]=topology.MIN_CAPACITY_BYTES
    r=topology.assess_topology(paths)
    assert r["filesystem_topology_flags"]["scratch_has_20g_current_headroom"]
    assert not r["scratch_capacity_reserved"]


def test_invalid_or_missing_path_inventory_fails_closed():
    paths=snapshot()
    del paths["ssd"]
    with pytest.raises(topology.UnsafeTopology):
        topology.assess_topology(paths)


def test_extra_untrusted_path_inventory_fails_closed():
    paths=snapshot()
    paths["new_production_credential_dir"]={}
    with pytest.raises(topology.UnsafeTopology):
        topology.assess_topology(paths)


@pytest.mark.parametrize("field,value", [
    ("host_path","/media/scott/unexpected"),
    ("resolved_path",0),
    ("is_mountpoint","true"),
    ("is_symlink",1),
    ("device_id",None),
    ("available_bytes_now","a lot"),
    ("directory_mode",None),
])
def test_malformed_path_evidence_fails_closed(field,value):
    paths=snapshot()
    paths["scratch"][field]=value
    with pytest.raises(topology.UnsafeTopology):
        topology.assess_topology(paths)


def test_ancestor_matching_disallows_similar_prefix():
    assert topology.within("/media/scott/SSD_4TB",
                           "/media/scott/SSD_4TB/knowledge")
    assert not topology.within("/media/scott/SSD_4TB",
                               "/media/scott/SSD_4TB-overwrite")


def test_reject_unapproved_observation_path(tmp_path):
    with pytest.raises(topology.UnsafeTopology):
        topology.analyze_path(tmp_path)


def test_findmnt_is_readonly_and_no_docker_operations(monkeypatch, tmp_path):
    # Hosted runners do not have x1-370's /home/scott/.cache directory.
    # Exercise the identical allowlisted-path contract with a real temporary
    # directory, while stubbing findmnt; never depend on a machine-specific
    # host directory or run a real findmnt in a unit test.
    monkeypatch.setattr(topology, "SCRATCH", tmp_path)
    monkeypatch.setitem(topology.EXPECTED_NODES, "scratch", tmp_path)
    fake=type("Result",(),{"returncode":0,
                           "stdout":"/ /dev/nvme0n1p6 ext4\n"})()
    calls=[]
    def run(cmd,**kwargs):
        calls.append(cmd)
        return fake
    monkeypatch.setattr(topology.subprocess,"run",run)
    data=topology.analyze_path(topology.SCRATCH)
    assert data["mount_target"] == "/"
    assert calls == [[topology.READONLY_COMMAND,"--target",
                      str(topology.SCRATCH),"--noheadings",
                      "--output","TARGET,SOURCE,FSTYPE"]]


def test_default_is_explicitly_hold():
    assert all("authorized" not in key for key in result()["filesystem_topology_flags"])
    for marker in topology.RELEASE_BLOCKERS:
        assert marker in result()["release_blockers"]


def test_unit_path_only_never_opens_protected_files():
    src=Path(topology.__file__).read_text()
    assert "os.statvfs(path)" in src
    assert "os.stat(path)" in src
    assert '"findmnt"' in src
    assert "os.listdir" not in src
    assert "docker exec" not in src
    assert "docker run" not in src


def test_actual_cifs_and_systemd_automount_layers_are_distinct_from_local_nas_label():
    rows=("/nas systemd-1 autofs\n"
          "/nas //192.168.1.202/fileserver cifs\n")
    proof=topology.qualify_network_mount_rows(rows)
    assert proof["remote_mount_observed"] is True
    assert proof["systemd_automount_layer_present"] is True
    assert proof["hermes_output_approved"] is False
    assert proof["independent_evidence_custody_verified"] is False
    assert topology.qualify_network_mount_rows(
        "/nas //192.168.1.202/fileserver cifs\n"
    )["systemd_automount_layer_present"] is False


@pytest.mark.parametrize("rows", [
    "",
    "/nas systemd-1 autofs\n",
    "/ /dev/nvme0n1p6 ext4\n",
    "/media/scott/NAS5 //192.168.1.202/fileserver cifs\n",
    "/nas //192.168.1.202/other-share cifs\n",
    "/nas //192.168.1.202/fileserver nfs4\n",
    "/nas other-systemd-unit autofs\n/nas //192.168.1.202/fileserver cifs\n",
    "/nas //192.168.1.202/fileserver cifs\n/nas //192.168.1.202/fileserver cifs\n",
    "/nas systemd-1 autofs\n/nas //192.168.1.202/fileserver cifs\n/nas unexpected ext4\n",
    "\n" * 10000,
])
def test_unmounted_wrong_source_and_ambiguous_network_proof_denied(rows):
    with pytest.raises(topology.UnsafeTopology):
        topology.qualify_network_mount_rows(rows)


def test_actual_nas_mount_metadata_readonly_without_custody_claim(monkeypatch, tmp_path):
    network=tmp_path/"nas"
    network.mkdir()
    monkeypatch.setattr(topology,"NETWORK_NAS",network)
    monkeypatch.setattr(topology.os.path,"ismount",lambda path: path==network)
    monkeypatch.setattr(topology,"NETWORK_NAS_SOURCE","//192.168.1.202/fileserver")
    rows=(f"{network} systemd-1 autofs\n"
          f"{network} //192.168.1.202/fileserver cifs\n")
    commands=[]
    def read(cmd,**kwargs):
        commands.append(cmd)
        return type("Result",(),{"returncode":0,"stdout":rows})()
    monkeypatch.setattr(topology.subprocess,"run",read)
    report=topology.analyze_network_nas()
    assert report["remote_mount_observed"]
    assert not report["available_bytes_is_reservation"]
    assert report["available_bytes_now"] >= 0
    assert report["hermes_output_approved"] is False
    assert commands==[["findmnt","--target",str(network),
                       "--noheadings","--output","TARGET,SOURCE,FSTYPE"]]


def _narrow_workspace(monkeypatch, tmp_path):
    network = tmp_path / "nas"
    network.mkdir(mode=0o700)
    parent = network / "projects"
    parent.mkdir(mode=0o700)
    workspace = parent / "hermes-workspace"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(topology, "NETWORK_NAS", network)
    def witness():
        return {"remote_mount_observed": True, "filesystem":"cifs",
                "expected_source": topology.NETWORK_NAS_SOURCE}
    monkeypatch.setattr(topology,"analyze_network_nas",witness)
    rows=(f"{network} systemd-1 autofs\n"
          f"{network} //192.168.1.202/fileserver cifs\n")
    monkeypatch.setattr(topology.subprocess,"run",lambda cmd,**kwargs:
                        type("Result",(),{"returncode":0,"stdout":rows})())
    return workspace,network


def test_narrow_workspace_metadata_is_not_deployment_approval(monkeypatch,tmp_path):
    workspace,_network=_narrow_workspace(monkeypatch,tmp_path)
    observed=topology.check_workspace_candidate(workspace)
    assert observed["prebind_remote_cifs_topology_qualified"] is True
    assert observed["hermes_output_approved"] is False
    assert observed["capacity_reserved"] is False
    assert observed["production_bind_or_mount_authorized"] is False
    assert observed["independent_custody_verified"] is False
    assert observed["requires_recheck_at_docker_bind_and_container_start"]


@pytest.mark.parametrize("defect",[
    "root","too_wide","outside","missing","symlink_parent",
    "symlink_leaf","wrong_mode","missing_remote","wrong_share",
    "witness_changes","wrong_caller","writable_parent",
    "nested_unreviewed_mount",
])
def test_workspace_fails_closed_for_unsafe_host_evidence(monkeypatch,tmp_path,defect):
    workspace,network=_narrow_workspace(monkeypatch,tmp_path)
    if defect=="root":
        workspace=network
    elif defect=="too_wide":
        workspace=network/"projects"
    elif defect=="outside":
        workspace=tmp_path/"outside"
    elif defect=="missing":
        workspace=workspace/"does-not-exist"
    elif defect=="symlink_parent":
        link=network/"linked"
        link.symlink_to(network/"projects",target_is_directory=True)
        workspace=link/"hermes-workspace"
    elif defect=="symlink_leaf":
        alias=network/"projects"/"alias"
        alias.symlink_to(workspace,target_is_directory=True)
        workspace=alias
    elif defect=="wrong_mode":
        workspace.chmod(0o755)
    elif defect=="missing_remote":
        monkeypatch.setattr(topology,"analyze_network_nas",
                            lambda:{"remote_mount_observed":False})
    elif defect=="wrong_share":
        monkeypatch.setattr(topology,"analyze_network_nas",
                            lambda:{"remote_mount_observed":True,
                                    "filesystem":"cifs",
                                    "expected_source":"//untrusted/share"})
    elif defect=="witness_changes":
        calls=[0]
        def unreliable():
            calls[0]+=1
            return {"remote_mount_observed": calls[0]==1,
                    "filesystem":"cifs",
                    "expected_source":topology.NETWORK_NAS_SOURCE}
        monkeypatch.setattr(topology,"analyze_network_nas",unreliable)
    elif defect=="wrong_caller":
        monkeypatch.setattr(topology.os,"geteuid",lambda:99999)
    elif defect=="writable_parent":
        (network/"projects").chmod(0o777)
    elif defect=="nested_unreviewed_mount":
        rows=(f"{workspace} //192.168.1.202/fileserver cifs\n")
        monkeypatch.setattr(topology.subprocess,"run",lambda cmd,**kwargs:
                            type("Result",(),{"returncode":0,"stdout":rows})())
    with pytest.raises(topology.UnsafeTopology):
        topology.check_workspace_candidate(workspace)


def test_workspace_checker_does_not_list_files_or_access_protected_corpus():
    import inspect
    source=inspect.getsource(topology.check_workspace_candidate)
    assert "os.lstat" in source and "analyze_network_nas()" in source
    for forbidden in ("os.listdir", "os.scandir", "read_bytes", "read_text",
                      "docker run", "systemctl", "mkdir(", "mount(", "open("):
        assert forbidden not in source
