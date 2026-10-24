"""Offline, fail-closed cross-host NAS5 topology-contract tests."""
from __future__ import annotations
import copy
import subprocess
from unittest.mock import Mock
import pytest
import hermes_nas5_crosshost_preflight as subject

SAMPLE = (
    "/nas /nas /dev/sdd2 btrfs\n"
    "/mnt/nas5-output-primary /mnt/nas5-output-primary /dev/sdb2 exfat\n"
    "/mnt/nas5-output-direct /mnt/nas5-output-direct /dev/sdc2 exfat\n"
    "/media/scott/NAS5 / /dev/nvme0n1p6 ext4\n"
)

def test_parse_explicit_remote_topology():
    rows=subject.parse_remote(SAMPLE)
    result=subject.assess("/",rows)
    assert result["beelink_three_expected_devices_mounted"] is True
    assert result["beelink_nas5_label_is_local_root"] is True
    assert result["x1_nas5_label_is_local_root"] is True
    assert result["decision"] == "HOLD"
    assert result["files_copied_or_mounted"] == 0

def test_reject_missing_one_remote_mount_row():
    with pytest.raises(subject.UnsafeCrossHost):
        subject.parse_remote("\n".join(SAMPLE.splitlines()[:-1]))

def test_reject_duplicated_remote_row():
    dup=SAMPLE.replace(SAMPLE.splitlines()[1],
                       SAMPLE.splitlines()[0])
    with pytest.raises(subject.UnsafeCrossHost):
        subject.parse_remote(dup)

@pytest.mark.parametrize("fragment", [
    "../nas /nas /dev/sdd2 btrfs",
    "/dev/nas /nas /dev/sdd2 btrfs",
    "/nas /nas /dev/sdd2 btrfs EXTRA",
    "/nas /nas /dev/sdd2",
    "/nas /nas /dev/sdd2 brfs!",
    "/nas /nas /dev/sdd2 btrfs\x00",
    "/nas /nas /dev/sdd2 btrfs\tmalicious",
    "/nas /nas /dev/sdd2 btrfs;foo",
    "/nas /nas /dev/sdd2 btrfs\r",
])
def test_reject_bogus_output(fragment):
    out=SAMPLE.replace(SAMPLE.splitlines()[0],fragment)
    with pytest.raises(subject.UnsafeCrossHost):
        subject.parse_remote(out)

def test_reject_oversized_remote_output():
    with pytest.raises(subject.UnsafeCrossHost):
        subject.parse_remote("X"*4097)

@pytest.mark.parametrize("local_target", ["", "relative", "0", None])
def test_local_target_not_trusted(local_target):
    with pytest.raises((subject.UnsafeCrossHost,AttributeError,TypeError)):
        subject.assess(local_target, subject.parse_remote(SAMPLE))

def test_missing_physical_mount_does_not_authorize():
    r=subject.parse_remote(SAMPLE)
    r["/nas"]["is_exact_mountpoint"]=False
    result=subject.assess("/",r)
    assert not result["beelink_three_expected_devices_mounted"]
    assert result["decision"]=="HOLD"

def test_wrong_filesystem_does_not_authorize():
    r=subject.parse_remote(SAMPLE)
    r["/nas"]["filesystem_type"]="ext4"
    result=subject.assess("/",r)
    assert not result["beelink_three_expected_devices_mounted"]
    assert result["decision"]=="HOLD"

def test_x1_nas5_if_mounted_still_hold():
    result=subject.assess("/media/scott/NAS5",subject.parse_remote(SAMPLE))
    assert not result["x1_nas5_label_is_local_root"]
    assert not result["production_deployment_authorized"]
    assert result["decision"]=="HOLD"

def test_status_never_elevates_to_approval():
    r=subject.assess("/",subject.parse_remote(SAMPLE))
    assert r["release_blockers"]
    assert "independent" in " ".join(r["release_blockers"])
    assert not r["production_mounts_modified"]
    assert r["data_files_read"]==0

def test_remote_script_contains_only_readonly_findmnt():
    script=subject.REMOTE_SCRIPT
    assert "findmnt -T" in script
    assert "mount " not in script
    assert "umount" not in script
    assert "rsync" not in script
    assert "sudo" not in script
    assert "cat " not in script
    assert "/nas" in script
    assert subject.SSH[:3]==["ssh","-n","-T"]
    assert "StrictHostKeyChecking=yes" in subject.SSH
    assert "BatchMode=yes" in subject.SSH

def test_dry_run_produces_no_network_or_docker(monkeypatch):
    def denied(*args,**kwargs):
        raise AssertionError("not a dry run")
    monkeypatch.setattr(subject.subprocess,"run",denied)
    # Default invocation has no subprocess work.
    import sys
    from pathlib import Path
    assert "--physical-readonly-crosshost" in Path(subject.__file__).read_text()
    assert subject.BLOCKERS

def test_physical_call_mocked_rejects_untrusted_ssh(monkeypatch):
    monkeypatch.setattr(subject.os,"geteuid",lambda:1000)
    monkeypatch.setattr(subject.os,"uname",lambda:Mock(nodename="x1-370"))
    calls=[]
    def fake(command,**kwargs):
        calls.append(command)
        if command[0]=="findmnt":
            return Mock(returncode=0,stdout="/\n")
        return Mock(returncode=255,stdout="",stderr="read-only SSH unavailable")
    monkeypatch.setattr(subject.subprocess,"run",fake)
    with pytest.raises(subject.UnsafeCrossHost,match="trusted Beelink"):
        subject.physical()
    assert len(calls)==2
    assert calls[1][:3]==["ssh","-n","-T"]

def test_physical_mocked_readonly_success_still_hold(monkeypatch):
    monkeypatch.setattr(subject.os,"geteuid",lambda:1000)
    monkeypatch.setattr(subject.os,"uname",lambda:Mock(nodename="x1-370"))
    calls=[]
    def fake(command,**kwargs):
        calls.append(command)
        return (Mock(returncode=0,stdout="/\n") if command[0]=="findmnt"
                else Mock(returncode=0,stdout=SAMPLE))
    monkeypatch.setattr(subject.subprocess,"run",fake)
    result=subject.physical()
    assert len(calls)==2
    assert result["decision"]=="HOLD"
    assert not result["production_deployment_authorized"]
    assert result["data_files_read"]==0
