"""Pure offline HOLD-only checks for the Hermes mount transition preflight."""
import copy
import pathlib
import pytest

import hermes_production_mount_migration_preflight as audit
import test_hermes_split_root_normal_compose_canary as sample

def mount(source, dest="/workspace", writable=True):
    return {"Source": source, "Destination": dest, "RW": writable}

def test_alias_to_corpus_is_dangerous():
    m = audit.classify(mount("/home/scott/knowledge", "/root/knowledge"),
                       resolver=lambda _: audit.PROTECTED)
    assert "writable_protected_corpus_or_ancestor" in m["risks"]
    assert "legacy_knowledge_root_writable" in m["risks"]
    assert "source_is_symlink_or_alias" in m["risks"]

def test_writable_parent_ssd_is_dangerous():
    m = audit.classify(mount(audit.SSD_ROOT, audit.SSD_ROOT))
    assert "writable_entire_ssd" in m["risks"]
    assert "writable_protected_corpus_or_ancestor" in m["risks"]

def test_writable_nas_root_is_dangerous():
    m = audit.classify(mount(audit.NAS_ROOT, audit.NAS_ROOT))
    assert "writable_nas_root_or_ancestor" in m["risks"]

def test_ro_private_ssh_still_needs_removal():
    m = audit.classify(mount("/home/scott/.ssh/id_hermes",
                             "/root/.ssh/id_hermes", False))
    assert "private_ssh_mount_not_allowed_in_candidate" in m["risks"]

def test_mutable_repo_and_live_src_denied():
    m = audit.classify(mount("/home/scott/git/auto-assist/src", "/app/src"))
    assert "mutable_runtime_application_source" in m["risks"]
    assert "mutable_repository_mount" in m["risks"]

@pytest.mark.parametrize("bad", [
    {}, {"Source": "relative", "Destination": "/x", "RW": True},
    {"Source": "/x", "Destination": "relative", "RW": True},
    {"Source": "/x", "Destination": "/x", "RW": "true"},
])
def test_untrusted_mount_metadata_denied(bad):
    with pytest.raises(audit.UnsafeAudit):
        audit.classify(bad)

def test_production_summary_redacts_environ_entirely():
    doc={"Mounts":[mount(audit.SSD_ROOT, audit.SSD_ROOT),
                   mount(audit.PROTECTED, "/root/knowledge"),
                   mount(audit.NAS_ROOT, audit.NAS_ROOT),
                   mount("/home/scott/git/source","/app/src")],
         "Image": audit.IMAGE, "State":{"Running":True},
         "Config":{"Env":["SECRET_TOKEN=actual-secret-value"]}}
    p = audit.inspect_production(doc)
    assert p["mount_count"]==4
    assert p["writable_mount_count"]==4
    assert p["environment_inspected"] is False
    assert "SECRET_TOKEN" not in str(p)
    assert "actual-secret-value" not in str(p)
    assert p["protected_corpus_writable_aliases"] == [
        audit.SSD_ROOT, "/root/knowledge"]
    assert p["nas_root_rw_present"]

def test_production_summary_requires_valid_metadata():
    with pytest.raises(audit.UnsafeAudit):
        audit.inspect_production({"Mounts":"not-list","State":{"Running":True}})

def test_mounts_classified_not_declared_release_safe():
    doc={"Mounts":[mount("/home/scott/scratch","/workspace")],
         "Image": audit.IMAGE,"State":{"Running":True}}
    summary=audit.inspect_production(doc)
    assert summary["risky_mount_count"] == 1
    assert "unreviewed_writable_mount" in summary["mounts"][0]["risks"]

def test_read_only_unrelated_file_not_rw():
    m = audit.classify(mount("/home/scott/readme","/readme",False))
    assert m["writable"] is False
    assert "unreviewed_writable_mount" not in m["risks"]

def test_no_ancestor_confusion():
    assert audit.is_inside("/root/storage", "/root/storage/child")
    assert not audit.is_inside("/root/storage", "/root/storage-evil")
    assert audit.accessible_ancestor(audit.SSD_ROOT,audit.PROTECTED)
    assert not audit.accessible_ancestor("/tmp/safe",audit.PROTECTED)

def test_synthetic_candidate_exact_mount_pair_only():
    assert audit.validate_candidate(sample._render(),sample.IMG,sample.RO,sample.RW)
    bad=copy.deepcopy(sample._render())
    bad["services"]["hermes-split-synthetic"]["volumes"][0]["read_only"]=False
    assert not audit.validate_candidate(bad,sample.IMG,sample.RO,sample.RW)

@pytest.mark.parametrize("override",[
    {"network_mode":"host"}, {"read_only":False},
    {"ports":["8123:8123"]}, {"privileged":True},
    {"restart":"always"}, {"image":"latest"},
])
def test_synthetic_candidate_rejects_unsafe_overrides(override):
    bad=copy.deepcopy(sample._render())
    bad["services"]["hermes-split-synthetic"].update(override)
    assert not audit.validate_candidate(bad,sample.IMG,sample.RO,sample.RW)

def test_candidate_bad_source_path_alias_rejected(tmp_path):
    ro=tmp_path/"ro";rw=tmp_path/"rw";ro.mkdir();rw.mkdir()
    alias=tmp_path/"ro_alias";alias.symlink_to(ro)
    assert not audit.validate_candidate(sample._render(),sample.IMG,alias,rw)

def test_candidate_bad_image_rejected():
    assert not audit.validate_candidate(sample._render(),"latest",sample.RO,sample.RW)

def test_default_is_hold_without_docker(monkeypatch):
    def denied(*_a,**_kw):raise AssertionError("dry-run invoked subprocess")
    monkeypatch.setattr(audit.subprocess,"run",denied)
    d=audit.default_report()
    assert d["decision"]=="HOLD"
    assert d["production_audit_executed"] is False
    assert d["production_deployment_authorized"] is False

def test_release_blockers_always_explicit():
    assert "production_broad_rw_storage_mounts_unremediated" in audit.EXPECTED_BAN
    assert "production_private_ssh_mounts_must_be_removed" in audit.EXPECTED_BAN
    assert "owner_authorized_compatibility_and_revocation_unverified" in audit.EXPECTED_BAN

def test_live_audit_uses_only_read_metadata_not_config_env():
    code=pathlib.Path(audit.__file__).read_text()
    assert '"{{json .Mounts}}"' in code
    assert '"{{.Image}} {{.State.Running}}"' in code
    assert 'Config.Env' not in code
    assert 'inspect", "--format", "{{json .}}"' not in code
    assert '"config", "--format", "json"' in code
