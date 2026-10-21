"""Fail-closed source contracts for the opt-in rootless synthetic Compose service."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

SOURCE=Path(__file__).resolve().parents[1]/"deploy/hermes-rootless"
COMPOSE=SOURCE/"compose.synthetic.yml"
SMOKE=SOURCE/"synthetic_smoke.sh"
SERVICE="hermes-rootless-synthetic"
KEYS={"profiles","image","pull_policy","entrypoint","command","restart","user",
      "init","network_mode","read_only","cap_drop","security_opt","pids_limit",
      "mem_limit","cpus","tmpfs","environment","volumes"}
VOLUME_SOURCES={
    "/research/knowledge":"${HERMES_SYNTHETIC_KNOWLEDGE:?synthetic read-only knowledge fixture required}",
    "/workspace":"${HERMES_SYNTHETIC_WORKSPACE:?synthetic private workspace required}"}
ENV={"HOME":"/workspace","HERMES_HOME":"/workspace/hermes-home",
     "HERMES_CONFIG_HOME":"/workspace/.hermes","PYTHONDONTWRITEBYTECODE":"1"}

def secure_service(doc):
    if not isinstance(doc,dict) or set(doc)!={"name","services"} or doc.get("name")!="assistx-hermes-synthetic-rootless":
        return False
    ss=doc.get("services")
    if not isinstance(ss,dict) or set(ss)!={SERVICE}:
        return False
    s=ss[SERVICE]
    if not isinstance(s,dict) or set(s)!=KEYS:
        return False
    fixed={
        "profiles":["synthetic-canary"],
        "image":"${HERMES_CANDIDATE_IMAGE:?reviewed local candidate image required}",
        "pull_policy":"never","entrypoint":["/entrypoint.sh"],
        "command":["/bin/sh","/workspace/synthetic_smoke.sh"],
        "restart":"no","user":"1000:1000","init":False,
        "network_mode":"none","read_only":True,"cap_drop":["ALL"],
        "security_opt":["no-new-privileges:true"],"pids_limit":32,
        "mem_limit":402653184,"cpus":0.5,
        "tmpfs":["/tmp:rw,nosuid,nodev,noexec,size=16777216"],"environment":ENV}
    if any(type(s.get(k)) is not type(v) or s.get(k)!=v for k,v in fixed.items()):
        return False
    vols=s["volumes"]
    if not isinstance(vols,list) or len(vols)!=2:
        return False
    seen=set()
    for v in vols:
        if not isinstance(v,dict) or set(v)!={"type","source","target","read_only","bind"}:
            return False
        target=v.get("target")
        if target not in VOLUME_SOURCES or target in seen:
            return False
        seen.add(target)
        if (v.get("type")!="bind" or v.get("source")!=VOLUME_SOURCES[target] or
            v.get("read_only") is not (target=="/research/knowledge") or
            v.get("bind")!={"create_host_path":False}):
            return False
    return seen==set(VOLUME_SOURCES)

class RootlessSyntheticComposeTests(unittest.TestCase):
    def setUp(self):
        self.data=yaml.safe_load(COMPOSE.read_text())
    def edited(self,key,value):
        d=copy.deepcopy(self.data)
        d["services"][SERVICE][key]=value
        return secure_service(d)
    def test_exact_manifest(self):
        self.assertTrue(secure_service(self.data))
    def test_banned_features_fail(self):
        for k,v in (
            ("network_mode","host"),("read_only",False),("user","0:0"),
            ("cap_drop",[]),("security_opt",[]),("restart","always"),
            ("pull_policy","always"),("profiles",[]),("entrypoint",["/bin/sh"]),
            ("command",["python3","-m","assistx.strict_executor_main"]),
            ("ports",["8000:8000"]),("privileged",True),("devices",["/dev/kvm"]),
            ("env_file",[".env"]),("volumes_from",["hermes-adapter"]),
            ("depends_on",["api"]),("extra_hosts",["host.docker.internal:host-gateway"]),
            ("build","."),("mem_limit",0),("cpus",4),("tmpfs",[]),
        ):
            with self.subTest(feature=k):
                self.assertFalse(self.edited(k,v))
    def test_broad_or_extra_mount_denied(self):
        for target,source in (("/workspace","/home/scott/.ssh"),
                              ("/research/knowledge","/media/scott/SSD_4TB")):
            d=copy.deepcopy(self.data)
            for m in d["services"][SERVICE]["volumes"]:
                if m["target"]==target:
                    m["source"]=source
            self.assertFalse(secure_service(d))
        d=copy.deepcopy(self.data)
        d["services"][SERVICE]["volumes"][0]["read_only"]=False
        self.assertFalse(secure_service(d))
        d=copy.deepcopy(self.data)
        d["services"][SERVICE]["volumes"].append({"type":"bind","source":"/home/scott","target":"/root","read_only":True,"bind":{"create_host_path":False}})
        self.assertFalse(secure_service(d))
    def test_environment_and_extra_service_denied(self):
        d=copy.deepcopy(self.data)
        d["services"][SERVICE]["environment"]["OPENAI_API_KEY"]="NO_REAL_SECRET"
        self.assertFalse(secure_service(d))
        d=copy.deepcopy(self.data)
        d["services"]["api"]={"image":"busybox"}
        self.assertFalse(secure_service(d))
    def test_smoke_has_bounded_write_denials(self):
        s=SMOKE.read_text()
        result=subprocess.run(["/bin/sh","-n",str(SMOKE)],capture_output=True,text=True,timeout=6)
        self.assertEqual(result.returncode,0,result.stderr)
        for word in ("NoNewPrivs","CapEff","/research/knowledge/protected-marker.txt",
                     "/etc/rootless-write-forbidden","/workspace/synthetic-result.txt",
                     "ROOTLESS_COMPOSE_STORAGE_ISOLATION_PASS"):
            self.assertIn(word,s)
        self.assertNotIn("curl ",s)
        self.assertNotIn("assistx.strict_executor_main",s)
    @unittest.skipUnless(shutil.which("docker"),"Docker CLI not available")
    def test_rendered_compose_config_only(self):
        with tempfile.TemporaryDirectory(prefix="rootless-compose-contract-") as d:
            root=Path(d)
            (root/"knowledge").mkdir()
            (root/"worktree").mkdir()
            env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),"HOME":str(root),
                 "HERMES_CANDIDATE_IMAGE":"synthetic-offline:never-pull",
                 "HERMES_SYNTHETIC_KNOWLEDGE":str(root/"knowledge"),
                 "HERMES_SYNTHETIC_WORKSPACE":str(root/"worktree")}
            cmd=["docker","compose","--env-file","/dev/null","-f",str(COMPOSE),
                 "--profile","synthetic-canary","config","--format","json"]
            proc=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=20)
            if proc.returncode and "not a docker command" in proc.stderr.lower():
                self.skipTest("Compose plugin missing")
            self.assertEqual(proc.returncode,0,proc.stderr[:260])
            s=json.loads(proc.stdout)["services"][SERVICE]
            self.assertEqual(s["network_mode"],"none")
            self.assertEqual(s["pull_policy"],"never")
            self.assertIs(s["read_only"],True)
            self.assertEqual(s["user"],"1000:1000")
            self.assertNotIn("ports",s)
            self.assertEqual(len(s["volumes"]),2)
            self.assertEqual({x["target"] for x in s["volumes"]},{"/workspace","/research/knowledge"})
            for x in s["volumes"]:
                self.assertIs(x.get("bind",{}).get("create_host_path",False),False)
                self.assertEqual(x.get("read_only",False),x["target"]=="/research/knowledge")

if __name__=="__main__":
    unittest.main()
