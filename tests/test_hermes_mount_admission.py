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
    other=tmp_path/"other";other.mkdir()
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
