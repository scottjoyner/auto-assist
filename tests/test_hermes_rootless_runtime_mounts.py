"""Negative, side-effect-free tests of exact Hermes Docker runtime mount policy."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE=Path(__file__).resolve().parents[1]/"deploy/hermes-rootless/verify_runtime_mounts.py"
SPEC=importlib.util.spec_from_file_location("verify_runtime_mounts",MODULE)
assert SPEC and SPEC.loader
guard=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

class HermesRuntimeMountPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="hermes-runtime-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.ro=self.root/"knowledge"
        self.rw=self.root/"workspace"
        self.ro.mkdir()
        self.rw.mkdir()
        self.image="sha256:"+"a"*64
        self.record={
            "Image":self.image,
            "Config":{"User":"1000:1000","Entrypoint":["/entrypoint.sh"],"ExposedPorts":None},
            "HostConfig":{
                "NetworkMode":"none","ReadonlyRootfs":True,
                "CapDrop":["ALL"],"CapAdd":None,
                "SecurityOpt":["no-new-privileges:true"],
                "Privileged":False,"PidMode":"","IpcMode":"private",
                "Devices":None,"Binds":None,"PidsLimit":32,
                "Memory":402653184,"NanoCpus":500000000,
                "PortBindings":{},
            },
            "Mounts":[
                {"Type":"bind","Destination":"/research/knowledge",
                 "Source":str(self.ro),"RW":False},
                {"Type":"bind","Destination":"/workspace",
                 "Source":str(self.rw),"RW":True},
            ],
        }

    def evaluate(self,record=None,**kwargs):
        return guard.audit(self.record if record is None else record,
                           readonly_source=kwargs.get("ro",str(self.ro)),
                           private_source=kwargs.get("rw",str(self.rw)),
                           image_id=kwargs.get("image",self.image))
    def test_exact_synthetic_container_passes_but_not_release(self):
        r=self.evaluate()
        self.assertTrue(r["synthetic_runtime_policy_pass"],r["violations"])
        self.assertEqual(r["decision"],"HOLD")
        self.assertFalse(r["production_deployment_authorized"])
        self.assertEqual(r["mount_count_observed"],2)
        self.assertEqual(r["writable_mount_count"],1)
    def test_default_no_approval(self):
        with patch.object(guard.subprocess,"run") as command:
            result=guard.audit(None)
        command.assert_not_called()
        self.assertFalse(result["synthetic_runtime_policy_pass"])
        self.assertIn("missing_approved_sources",result["violations"])
    def test_denied_network_privilege_and_root(self):
        for part,key,changed in [
            ("Config","User","0:1000"),
            ("HostConfig","NetworkMode","host"),
            ("HostConfig","ReadonlyRootfs",False),
            ("HostConfig","CapDrop",[]),
            ("HostConfig","CapAdd",["SYS_ADMIN"]),
            ("HostConfig","SecurityOpt",[]),
            ("HostConfig","Privileged",True),
            ("HostConfig","Devices",[{"PathOnHost":"/dev/sda"}]),
            ("HostConfig","PidMode","host"),
            ("HostConfig","IpcMode","host"),
            ("HostConfig","PortBindings",{"80/tcp":[{"HostPort":"80"}]}),
            ("HostConfig","PidsLimit",0),
            ("HostConfig","Memory",0),
            ("HostConfig","NanoCpus",0),
        ]:
            with self.subTest(part=part,key=key):
                record=copy.deepcopy(self.record)
                record[part][key]=changed
                self.assertFalse(self.evaluate(record)["synthetic_runtime_policy_pass"])
    def test_extra_mount_and_writable_knowledge_denied(self):
        for mutant in ("extra","knowledge_rw","host_ssh","duplicate"):
            with self.subTest(mutant=mutant):
                rec=copy.deepcopy(self.record)
                if mutant=="extra":
                    rec["Mounts"].append({"Type":"bind","Destination":"/root/knowledge",
                                          "Source":str(self.root),"RW":False})
                elif mutant=="knowledge_rw":
                    rec["Mounts"][0]["RW"]=True
                elif mutant=="host_ssh":
                    rec["Mounts"][1]["Source"]="/home/scott/.ssh"
                elif mutant=="duplicate":
                    rec["Mounts"][1]["Destination"]="/research/knowledge"
                self.assertIn("mount_scope_violation",self.evaluate(rec)["violations"])
    def test_inherited_expose_metadata_not_a_published_port(self):
        rec=copy.deepcopy(self.record)
        rec["Config"]["ExposedPorts"]={"9000/tcp":{}}
        self.assertTrue(self.evaluate(rec)["synthetic_runtime_policy_pass"])
        rec["HostConfig"]["PortBindings"]={"9000/tcp":[{"HostPort":"9000"}]}
        self.assertIn("unexpected_ports",self.evaluate(rec)["violations"])

    def test_image_not_pinned_denied(self):
        for image in ("image:latest","sha256:"+"z"*64,None):
            with self.subTest(image=image):
                self.assertFalse(self.evaluate(image=image)["synthetic_runtime_policy_pass"])
        rec=copy.deepcopy(self.record)
        rec["Image"]="sha256:"+"b"*64
        self.assertIn("incorrect_image",self.evaluate(rec)["violations"])
    def test_path_aliases_and_symlinks_denied(self):
        alias=self.root/"alias"
        alias.symlink_to(self.ro,target_is_directory=True)
        self.assertIn("missing_approved_sources",
                      self.evaluate(ro=str(alias))["violations"])
        self.assertIn("missing_approved_sources",
                      self.evaluate(rw=str(self.root/"other"))["violations"])
    def test_sources_must_be_distinct(self):
        self.assertFalse(self.evaluate(rw=str(self.ro))["synthetic_runtime_policy_pass"])
    def test_raw_inspect_sources_never_echoed(self):
        result=self.evaluate()
        data=str(result)
        self.assertNotIn(str(self.ro),data)
        self.assertNotIn(str(self.rw),data)
        self.assertNotIn("SYNTHETIC_CREDENTIAL",data)
    def test_legacy_shape_rejected_without_paths(self):
        rec=copy.deepcopy(self.record)
        rec["HostConfig"]["NetworkMode"]="fleet_default"
        rec["Config"]["User"]="0:1000"
        rec["Mounts"]=[{"Destination":"/media/scott/NAS5","Source":"/media/scott/NAS5",
                        "RW":True,"Type":"bind"}]*16
        result=guard.audit(rec)
        self.assertFalse(result["synthetic_runtime_policy_pass"])
        self.assertEqual(result["mount_count_observed"],16)
        self.assertEqual(result["writable_mount_count"],16)
        self.assertNotIn("/media/scott",str(result))
    def test_unexpected_entrypoint_and_binds_rejected(self):
        a=copy.deepcopy(self.record)
        a["Config"]["Entrypoint"]=["/bin/sh"]
        self.assertIn("unexpected_entrypoint",self.evaluate(a)["violations"])
        a=copy.deepcopy(self.record)
        a["HostConfig"]["Binds"]=[str(self.ro)+":/research/knowledge:rw"]
        self.assertIn("host_namespace_or_device",self.evaluate(a)["violations"])

if __name__=="__main__":
    unittest.main()
