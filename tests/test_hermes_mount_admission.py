"""Kernel-backed mount isolation contract: synthetic read-only evidence only."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from assistx import knowledge_roots as roots


def mounted(tmp_path):
    read=tmp_path/"research"/"knowledge"
    output=tmp_path/"work"/"knowledge"
    root=output.parent
    read.mkdir(parents=True)
    output.mkdir(parents=True)
    return read, output, root

def line(number, where, mode="rw"):
    return f"{number} 1 0:{number} / {where} {mode},relatime - tmpfs tmpfs {mode}\n"

def evidence(read, write, *, read_mode="ro", write_mode="rw"):
    return (line(1,"/") + line(2,str(read),read_mode)
            + line(3,str(write),write_mode))

def test_exact_ro_source_and_rw_scratch_are_accepted(tmp_path):
    read,output,mount=mounted(tmp_path)
    assert roots.verify_mount_admission(str(read),str(output),
        write_mount=str(mount),evidence=evidence(read,mount)) is None

@pytest.mark.parametrize("bad", [
    lambda ro,rw,m: line(1,"/") + line(3,str(m),"rw"), # absent corpus bind
    lambda ro,rw,m: line(1,"/") + line(2,str(ro),"ro"), # absent scratch bind
    lambda ro,rw,m: evidence(ro,m,read_mode="rw"),
    lambda ro,rw,m: evidence(ro,m,write_mode="ro"),
    lambda ro,rw,m: line(1,"/") + line(2,str(ro),"ro") + line(2,str(m),"rw"),
    lambda ro,rw,m: evidence(ro,m) + line(4,str(ro/"nested"),"rw"),
    lambda ro,rw,m: evidence(ro,m) + line(4,str(m/"nested"),"rw"),
    lambda ro,rw,m: evidence(ro,m) + line(4,str(m/"nested"),"ro"),
    lambda ro,rw,m: evidence(ro,m) + line(4,str(ro),"ro"), # duplicate mount
    lambda ro,rw,m: evidence(ro,m).replace(" ro,relatime", " ro,rw,relatime"),
    lambda ro,rw,m: "",
    lambda ro,rw,m: "invalid",
    lambda ro,rw,m: "x" * 1000001,
])
def test_reject_missing_mismatched_nested_or_malformed_mounts(tmp_path,bad):
    read,output,mount=mounted(tmp_path)
    with pytest.raises(ValueError):
        roots.verify_mount_admission(str(read),str(output),
            write_mount=str(mount),evidence=bad(read,output,mount))

def test_stale_directory_without_actual_bind_denied(tmp_path):
    read,output,mount=mounted(tmp_path)
    (read/"sample.txt").write_text("synthetic only")
    with pytest.raises(ValueError,match="kernel"):
        roots.verify_mount_admission(str(read),str(output),
            write_mount=str(mount),
            evidence=line(1,"/") + line(3,str(mount),"rw"))

def test_crossed_mount_root_or_same_mountpoint_denied(tmp_path):
    read,output,mount=mounted(tmp_path)
    for root in ("/", "/home", "/mnt", str(read), str(output)):
        with pytest.raises(ValueError):
            roots.verify_mount_admission(str(read),str(output),
                write_mount=root,evidence=evidence(read,mount))

def test_write_root_must_be_contained_under_declared_rw_mount(tmp_path):
    read,output,mount=mounted(tmp_path)
    other=tmp_path/"other"
    other.mkdir()
    with pytest.raises(ValueError):
        roots.verify_mount_admission(str(read),str(output),
            write_mount=str(other),evidence=evidence(read,mount))

def test_input_root_symlink_alias_rejected(tmp_path):
    read,output,mount=mounted(tmp_path)
    alias=tmp_path/"corpus-alias"
    alias.symlink_to(read,target_is_directory=True)
    with pytest.raises(ValueError,match="symbolic"):
        roots.verify_mount_admission(str(alias),str(output),
            write_mount=str(mount),evidence=evidence(read,mount))

def test_write_mount_symlink_alias_rejected(tmp_path):
    read,output,mount=mounted(tmp_path)
    alias=tmp_path/"workspace-alias"
    alias.symlink_to(mount,target_is_directory=True)
    with pytest.raises(ValueError,match="symbolic"):
        roots.verify_mount_admission(str(read),str(output),
            write_mount=str(alias),evidence=evidence(read,mount))

def test_requires_dir_even_if_mountinfo_claims_it(tmp_path):
    read,output,mount=mounted(tmp_path)
    read.rmdir()
    with pytest.raises(ValueError,match="missing"):
        roots.verify_mount_admission(str(read),str(output),
            write_mount=str(mount),evidence=evidence(read,mount))

def test_environment_enables_kernel_gate_and_denies_missing_bind(monkeypatch,tmp_path):
    ro,rw,m=mounted(tmp_path)
    with monkeypatch.context() as mp:
        mp.setenv(roots.READ_ENV,str(ro))
        mp.setenv(roots.WRITE_ENV,str(rw))
        mp.setenv(roots.WRITE_MOUNT_ENV,str(m))
        mp.setenv(roots.MOUNT_GUARD_ENV,"true")
        # tmp dirs exist but are not actual mounts in this test namespace.
        with pytest.raises(ValueError):
            roots.resolve_knowledge_roots("/legacy",str(rw/"eval.json"))

def test_env_without_split_roots_fails_closed(monkeypatch):
    with monkeypatch.context() as mp:
        mp.delenv(roots.READ_ENV,raising=False)
        mp.delenv(roots.WRITE_ENV,raising=False)
        mp.setenv(roots.MOUNT_GUARD_ENV,"true")
        with pytest.raises(ValueError,match="split roots"):
            roots.resolve_knowledge_roots("/legacy")

@pytest.mark.parametrize("value",["TRUE","on","1","invalid"])
def test_invalid_strict_guard_flag_fails_closed(monkeypatch,tmp_path,value):
    ro,rw,m=mounted(tmp_path)
    with monkeypatch.context() as mp:
        mp.setenv(roots.READ_ENV,str(ro))
        mp.setenv(roots.WRITE_ENV,str(rw))
        mp.setenv(roots.MOUNT_GUARD_ENV,value)
        with pytest.raises(ValueError,match="mode"):
            roots.resolve_knowledge_roots("/legacy",str(rw/"eval.json"))

def test_legacy_mode_default_does_not_claim_mount_provenance(monkeypatch):
    with monkeypatch.context() as mp:
        mp.delenv(roots.READ_ENV,raising=False)
        mp.delenv(roots.WRITE_ENV,raising=False)
        mp.delenv(roots.MOUNT_GUARD_ENV,raising=False)
        assert roots.resolve_knowledge_roots("/legacy")==("/legacy","/legacy",False)

def test_parser_rejects_escaped_mounts_and_malformed_records():
    for value in ("1 0 0:1 / /some\\040alias ro - ext4 /dev/x ro",
                  "1 0 0:1 / /x ro - ext4 /dev/x",
                  "1 0 0:1 / /x ro,rw - ext4 /dev/x ro"):
        with pytest.raises(ValueError):
            roots._mount_records(value)

def test_mount_policy_does_not_read_sensitive_paths():
    source=Path(roots.__file__).read_text()
    assert '"/proc/self/mountinfo"' in source
    assert "os.listdir" not in source
    assert "os.walk" not in source
    assert "docker exec" not in source
    assert "ssh " not in source


REMOTE = "//192.168.1.202/fileserver"


def remote_mountinfo(read, mount, *, fs="cifs", source=REMOTE):
    return (line(1,"/") + line(2,str(read),"ro")
            + f"3 1 0:3 / {mount} rw,relatime - {fs} {source} rw\n")


def test_explicit_remote_source_is_correlated_with_kernel_mountinfo(tmp_path):
    read,output,mount=mounted(tmp_path)
    assert roots.verify_mount_admission(
        str(read),str(output),write_mount=str(mount),
        evidence=remote_mountinfo(read,mount),
        expected_write_identity=("cifs",REMOTE)) is None
    # Without the opt-in identity check, the original isolation contract
    # continues to work with the disposable synthetic tmpfs fixture.
    assert roots.verify_mount_admission(
        str(read),str(output),write_mount=str(mount),
        evidence=evidence(read,mount)) is None


@pytest.mark.parametrize("fs,source",[
    ("ext4","/dev/nvme0n1p6"),
    ("tmpfs","tmpfs"),
    ("autofs","systemd-1"),
    ("nfs4","192.168.1.79:/tank/recovery"),
    ("cifs","//192.168.1.202/other-share"),
    ("cifs","//192.168.1.203/fileserver"),
    ("cifs","//192.168.1.202/fileserver-backup"),
])
def test_remote_source_expected_but_wrong_backing_is_denied(tmp_path,fs,source):
    read,output,mount=mounted(tmp_path)
    with pytest.raises(ValueError,match="backing source"):
        roots.verify_mount_admission(
            str(read),str(output),write_mount=str(mount),
            evidence=remote_mountinfo(read,mount,fs=fs,source=source),
            expected_write_identity=("cifs",REMOTE))


@pytest.mark.parametrize("wanted",[
    ("ext4","/dev/nvme0n1p6"),
    ("cifs","/local/root"),
    ("cifs","//192.168.1.202/fileserver/extra/path"),
    ("cifs","//192.168.1.202/a\\040b"),
    ("nfs4","192.168.1.79:/tank/recovery"),
    ("",REMOTE),
    ("cifs", ""),
])
def test_malformed_or_unapproved_requested_remote_identity_rejected(tmp_path,wanted):
    read,output,mount=mounted(tmp_path)
    with pytest.raises(ValueError,match="remote CIFS"):
        roots.verify_mount_admission(
            str(read),str(output),write_mount=str(mount),
            evidence=remote_mountinfo(read,mount),expected_write_identity=wanted)


def test_remote_identity_must_be_fully_configured_with_strict_mount_guard(
        monkeypatch,tmp_path):
    read,output,mount=mounted(tmp_path)
    monkeypatch.setenv(roots.READ_ENV,str(read))
    monkeypatch.setenv(roots.WRITE_ENV,str(output))
    monkeypatch.setenv(roots.WRITE_MOUNT_ENV,str(mount))
    monkeypatch.setenv(roots.EXPECTED_WRITE_FSTYPE_ENV,"cifs")
    with pytest.raises(ValueError,match="both write filesystem"):
        roots.resolve_knowledge_roots("/unused")
    monkeypatch.setenv(roots.EXPECTED_WRITE_SOURCE_ENV,REMOTE)
    with pytest.raises(ValueError,match="strict kernel mount"):
        roots.resolve_knowledge_roots("/unused")
    monkeypatch.setenv(roots.MOUNT_GUARD_ENV,"true")
    # Real temp directories are not kernel binds: must fail before any use.
    with pytest.raises(ValueError,match="kernel|mountpoint|mount evidence"):
        roots.resolve_knowledge_roots("/unused")
    monkeypatch.delenv(roots.READ_ENV)
    monkeypatch.delenv(roots.WRITE_ENV)
    with pytest.raises(ValueError,match="strict split roots"):
        roots.resolve_knowledge_roots("/unused")


def test_remote_source_check_is_readonly_and_cannot_claim_storage_approval():
    src=Path(roots.__file__).read_text()
    assert "_require_write_mount_identity(evidence, mount" in src
    assert '"/proc/self/mountinfo"' in src
    assert "expected remote CIFS mount identity is invalid" in src
    for prohibited in ("docker run", "docker exec", "mount -t", "os.system",
                       "shutil.copytree", "os.listdir"):
        assert prohibited not in src



def test_remote_cifs_mount_rechecked_before_each_covered_output(monkeypatch,tmp_path):
    import builtins
    import io
    read,output,mount=mounted(tmp_path)
    monkeypatch.setenv(roots.READ_ENV,str(read))
    monkeypatch.setenv(roots.WRITE_ENV,str(output))
    monkeypatch.setenv(roots.WRITE_MOUNT_ENV,str(mount))
    monkeypatch.setenv(roots.MOUNT_GUARD_ENV,"true")
    monkeypatch.setenv(roots.EXPECTED_WRITE_FSTYPE_ENV,"cifs")
    monkeypatch.setenv(roots.EXPECTED_WRITE_SOURCE_ENV,REMOTE)
    evidence_now=[remote_mountinfo(read,mount)]
    attempts=[]
    original=builtins.open
    def synthetic_mount_evidence(path,*args,**kwargs):
        if path=="/proc/self/mountinfo":
            attempts.append(1)
            return io.StringIO(evidence_now[0])
        return original(path,*args,**kwargs)
    monkeypatch.setattr(builtins,"open",synthetic_mount_evidence)
    target=output/"notes.md"
    assert roots.checked_write_path(str(output),str(target))==str(target)
    assert len(attempts)==1
    evidence_now[0]=remote_mountinfo(read,mount,fs="ext4",source="/dev/local-root")
    with pytest.raises(ValueError,match="backing source"):
        roots.checked_write_path(str(output),str(target))
    assert len(attempts)==2
    assert not target.exists()
    # If mounted again, the independent metadata check is repeated, not cached.
    evidence_now[0]=remote_mountinfo(read,mount)
    assert roots.checked_write_path(str(output),str(target))==str(target)
    assert len(attempts)==3


@pytest.mark.parametrize("missing",[
    roots.READ_ENV,roots.WRITE_ENV,roots.WRITE_MOUNT_ENV,
    roots.EXPECTED_WRITE_FSTYPE_ENV,roots.EXPECTED_WRITE_SOURCE_ENV,
])
def test_recheck_denies_incomplete_remote_configuration(monkeypatch,tmp_path,missing):
    read,output,mount=mounted(tmp_path)
    settings={roots.READ_ENV:str(read),roots.WRITE_ENV:str(output),
              roots.WRITE_MOUNT_ENV:str(mount),roots.MOUNT_GUARD_ENV:"true",
              roots.EXPECTED_WRITE_FSTYPE_ENV:"cifs",
              roots.EXPECTED_WRITE_SOURCE_ENV:REMOTE}
    for key,val in settings.items():
        monkeypatch.setenv(key,val)
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError,match="fully configured"):
        roots.checked_write_path(str(output),str(output/"notes.md"))
    assert not (output/"notes.md").exists()


def test_legacy_checked_write_path_remains_independent_of_new_guard(monkeypatch,tmp_path):
    _,output,_=mounted(tmp_path)
    monkeypatch.delenv(roots.EXPECTED_WRITE_FSTYPE_ENV,raising=False)
    monkeypatch.delenv(roots.EXPECTED_WRITE_SOURCE_ENV,raising=False)
    monkeypatch.setenv(roots.MOUNT_GUARD_ENV,"false")
    assert roots.checked_write_path(str(output),str(output/"notes.md"))==str(output/"notes.md")
