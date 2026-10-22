#!/usr/bin/env python3
"""Opt-in exact-source, normal-entrypoint, restricted-Compose Hermes canary.

Never alters a production Compose project, secrets, networks or mounts.
Only an inspected synthetic profile and current pinned local image are used.
"""
from __future__ import annotations
import argparse
import hashlib
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import uuid

import hermes_split_root_container_canary as prior

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "assistx"
FIXTURE = pathlib.Path(__file__).resolve().parent
COMPOSE = FIXTURE / "hermes_split_root_normal.compose.yaml"
ENTRY = FIXTURE / "hermes_split_root_candidate_entrypoint.sh"
SITE = FIXTURE / "hermes_split_root_normal_sitecustomize.py"
EXPECTED_ENTRY_SHA = "81add7ecd43ef1b3fccc5d8097b4f51ed196cfb61d5ccd3cd243e51a552ab6f5"
BRANCH = "fix/hermes-split-knowledge-roots-20261021"
PINNED_HEAD = "412affd187acffe2147f03088e1e126472f4667e"
IMAGE = prior.IMAGE_ID
LIVE = prior.LIVE
SCRATCH = prior.SCRATCH
CHECKS = (
    "exact_published_source_and_entrypoint",
    "networkless_offline_overlay_build",
    "restricted_compose_render_verified",
    "kernel_ro_input_rw_scratch_admission_verified",
    "signed_corpus_mutation_denied_at_relaunch",
    "older_signed_manifest_replay_denied",
    "effective_container_mount_and_security_verified",
    "normal_entrypoint_strict_executor_twice",
    "actual_patched_selftask_read_write_restart",
    "zero_real_tasks_and_providers",
    "all_disposable_docker_objects_removed",
    "production_hermes_running_unchanged",
)
BLOCKERS = (
    "production_writable_ancestors_unremediated",
    "independent_signed_image_and_source_attestation_missing",
    "real_owner_authorized_ingest_and_task_acceptance_missing",
    "independent_revocation_fencing_and_rollback_unverified",
    "independent_reviewer_and_release_approval_missing",
)

class UnsafeCanary(RuntimeError):
    pass

def need(cond, why):
    if not cond:
        raise UnsafeCanary(why)

def cmd(args, seconds=20, env=None):
    return subprocess.run(args, check=False, text=True, capture_output=True,
                          timeout=seconds, env=env)

def sha(path):
    need(path.is_file() and not path.is_symlink(), "missing or redirected source file")
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read_only_source():
    need(os.uname().nodename == "x1-370" and os.geteuid() == 1000,
         "physical canary restricted to x1-370 UID 1000")
    need(cmd(["git", "-C", str(ROOT), "merge-base", "--is-ancestor",
              PINNED_HEAD, "HEAD"], 6).returncode == 0,
         "research source does not descend from tested PR #425 commit")
    # Source may be accompanied by this new test commit; the three actual
    # production modules MUST retain their exact reviewed PR #425 blobs.
    expected_blobs = {
        "src/assistx/agents/hermes_agent_adapter.py":
            "ce3a96cee9656bc0818b5ee385aa9d0c44058a21",
        "src/assistx/swarm_memory.py":
            "9b073d1f8e31d98e08820d473523abbf5d716818",
         "src/assistx/knowledge_roots.py":
            "6ecdce980c6a5513014088cd7e3ae57b3f337216",
        "src/assistx/hermes_corpus_custody.py":
            "e504334468ae0962668f2abd37978532483ec29b",
        "src/assistx/hermes_corpus_checkpoint.py":
            "ff7091618919b2261b1cbf4df2602627afae8c1f",
        "src/assistx/hermes_high_water.py":
            "ed8992eef765006cb6a12def4553b04d0129ecbe",
        "src/assistx/strict_executor_adapter.py":
            "ee224f50ae93c0eb8427e0db496f3a76172f3f05",
        "src/assistx/hermes_launch_gate.py":
            "9fe5214ef51efb3aee2e1f08f5150a1a056fed88",
    }
    for path, exact_blob in expected_blobs.items():
        observed = cmd(["git", "-C", str(ROOT), "hash-object",
                        str(ROOT / path)], 7)
        need(observed.returncode == 0 and observed.stdout.strip() == exact_blob,
             "reviewed source blob differs: " + path)
    need(sha(ENTRY) == EXPECTED_ENTRY_SHA, "rootless entrypoint differs from reviewed fixture")
    need(SITE.is_file() and COMPOSE.is_file(), "missing reviewable fixture")
    before = cmd(["docker", "inspect", "-f", "{{.Image}} {{.State.Running}}", LIVE], 7)
    need(before.returncode == 0 and before.stdout.strip() == IMAGE + " true",
         "production Hermes state changed")
    return before.stdout.strip()

def restricted_render(doc, image, corpus, scratch):
    if not isinstance(doc, dict) or set(doc.get("services", {})) != {"hermes-split-synthetic"}:
        return False
    svc = doc["services"]["hermes-split-synthetic"]
    banned = {"ports","expose","secrets","env_file","build","depends_on","devices",
              "network","networks","extra_hosts","volumes_from","privileged","pid","ipc"}
    if (banned.intersection(svc)
         or svc.get("image") != image or svc.get("pull_policy") != "never"
         or svc.get("entrypoint") != ["/entrypoint.sh"]
         or svc.get("command") != ["python3","-B","-m","assistx.strict_executor_main"]
         or svc.get("user") != "1000:1000"
         or svc.get("network_mode") != "none"
         or svc.get("read_only") is not True
         or svc.get("restart") not in ("no",None)
         or svc.get("profiles") != ["synthetic-canary"]
         or "ALL" not in (svc.get("cap_drop") or [])
         or "no-new-privileges:true" not in (svc.get("security_opt") or [])):
        return False
    expected_env = {
        "HOME":"/workspace","HERMES_HOME":"/workspace/hermes-home",
        "HERMES_CONFIG_HOME":"/workspace/.hermes",
        "PYTHONPATH":"/workspace:/app/src",
        "ASSISTX_OUTBOX_DB":"/workspace/outbox.sqlite",
        "ASSISTX_URL":"http://127.0.0.1:1",
        "ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN":"SYNTHETIC_BOOTSTRAP_ONLY",
        "ASSISTX_STRICT_EXECUTOR_AUTH":"true",
        "ASSISTX_REQUIRE_OWNER_BINDING":"true",
        "HERMES_REQUIRE_OWNER_BOUND_LAUNCH":"true",
        "ASSISTX_EXECUTOR_VERIFY_KEY_FILE":"/app/attestation/executor.pub",
        "HERMES_AGENT_ID":"hermes-synthetic-canary",
        "HERMES_SELFTASK_ENABLED":"false",
        "HERMES_SELFTASK_BULK_MODELS":"synthetic/model",
        "HERMES_KNOWLEDGE_READ_ROOT":"/research/knowledge",
        "HERMES_KNOWLEDGE_WRITE_ROOT":"/workspace/knowledge",
        "HERMES_REQUIRE_MOUNT_ISOLATION":"true",
        "HERMES_REQUIRE_SIGNED_CORPUS":"true",
        "HERMES_REQUIRE_CORPUS_CHECKPOINT":"true",
        "HERMES_CORPUS_CHECKPOINT_PATH":"/app/attestation/checkpoint.json",
        "HERMES_CORPUS_WITNESS_KEY_PATH":"/app/attestation/witness.pub",
        "HERMES_CORPUS_TRUST_KEY_PATH":"/app/attestation/trust.pub",
        "HERMES_KNOWLEDGE_WRITE_MOUNT":"/workspace",
        "HERMES_KNOWLEDGE_ROOT":"/workspace/knowledge",
        "HERMES_EVAL_PATH":"/workspace/knowledge/eval.json",
        "PYTHONDONTWRITEBYTECODE":"1",
    }
    if svc.get("environment") != expected_env:
        return False
    v = svc.get("volumes")
    if not isinstance(v,list) or len(v)!=2:
        return False
    mounts = {m.get("target"):(m.get("type"),m.get("source"),
              m.get("read_only",False)) for m in v if isinstance(m,dict)}
    return (len(mounts)==2 and mounts=={
        "/research/knowledge":("bind",str(corpus),True),
        "/workspace":("bind",str(scratch),False)})

def inspect_created(cid, image, project, corpus, work):
    r = cmd(["docker","inspect","--format","{{json .}}",cid],7)
    need(r.returncode==0 and len(r.stdout)<80000,"could not inspect disposable container")
    j = json.loads(r.stdout)
    hc, cfg, mounts = j["HostConfig"], j["Config"], j["Mounts"]
    need(j["Image"]==image and cfg["User"]=="1000:1000" and
         cfg["Entrypoint"]==["/entrypoint.sh"] and
         cfg["Cmd"]==["python3","-B","-m","assistx.strict_executor_main"] and
         cfg["Labels"].get("com.docker.compose.project")==project and
         cfg["Labels"].get("com.docker.compose.service")=="hermes-split-synthetic" and
         hc["NetworkMode"]=="none" and hc["ReadonlyRootfs"] is True and
         hc["Privileged"] is False and hc["PidsLimit"]==32 and
         hc["Memory"]==402653184 and hc["NanoCpus"]==500000000 and
         "ALL" in hc["CapDrop"] and
         "no-new-privileges" in " ".join(hc["SecurityOpt"] or []) and
         not hc.get("PortBindings"), "disposable container escape contract")
    paths={v["Destination"]:(v["Source"],v["RW"]) for v in mounts}
    need(paths=={"/research/knowledge":(str(corpus),False),
                 "/workspace":(str(work),True)}, "unexpected bind source or write permission")

def run():
    before=read_only_source()
    suffix=uuid.uuid4().hex[:16]
    project="oghermessplit"+suffix
    base="og-hermes-split-normal-base-"+suffix
    tag="og-hermes-split-normal-overlay-"+suffix
    create_attempted=False
    images=[]
    checks={k:False for k in CHECKS}
    with tempfile.TemporaryDirectory(prefix="og-hermes-containment-",dir=SCRATCH) as tmp:
        folder=pathlib.Path(tmp)
        folder.chmod(0o755)
        ctx=folder/"build"
        ctx.mkdir()
        for a,b in (
            (SOURCE/"knowledge_roots.py",ctx/"knowledge_roots.py"),
            (SOURCE/"swarm_memory.py",ctx/"swarm_memory.py"),
            (SOURCE/"hermes_corpus_custody.py",ctx/"hermes_corpus_custody.py"),
            (SOURCE/"hermes_corpus_checkpoint.py",ctx/"hermes_corpus_checkpoint.py"),
            (SOURCE/"hermes_high_water.py",ctx/"hermes_high_water.py"),
            (SOURCE/"strict_executor_adapter.py",ctx/"strict_executor_adapter.py"),
            (SOURCE/"hermes_launch_gate.py",ctx/"hermes_launch_gate.py"),
            (SOURCE/"agents/hermes_agent_adapter.py",ctx/"hermes_agent_adapter.py"),
            (ENTRY,ctx/"entrypoint.sh")):
            shutil.copyfile(a,b)
            b.chmod(0o644)
            need(sha(a)==sha(b),"staged source mismatch")
        df=(f"FROM {base}\n"
            "COPY knowledge_roots.py /app/src/assistx/knowledge_roots.py\n"
            "COPY swarm_memory.py /app/src/assistx/swarm_memory.py\n"
            "COPY hermes_corpus_custody.py /app/src/assistx/hermes_corpus_custody.py\n"
            "COPY hermes_corpus_checkpoint.py /app/src/assistx/hermes_corpus_checkpoint.py\n"
            "COPY hermes_high_water.py /app/src/assistx/hermes_high_water.py\n"
            "COPY strict_executor_adapter.py /app/src/assistx/strict_executor_adapter.py\n"
            "COPY hermes_launch_gate.py /app/src/assistx/hermes_launch_gate.py\n"
            "COPY executor.pub /app/attestation/executor.pub\n"
            "COPY witness.pub /app/attestation/witness.pub\n"
            "COPY checkpoint.json /app/attestation/checkpoint.json\n"
            "COPY trust.pub /app/attestation/trust.pub\n"
            "COPY hermes_agent_adapter.py /app/src/assistx/agents/hermes_agent_adapter.py\n"
            "COPY entrypoint.sh /entrypoint.sh\n"
            "RUN chmod 0755 /entrypoint.sh\n")
        (ctx/"Dockerfile").write_text(df)
        corpus=folder/"knowledge"
        work=folder/"worktree"
        corpus.mkdir()
        work.mkdir()
        (corpus/"decision.md").write_text("SYNTHETIC_CONTEXT_42\n")
        (corpus/"decision.md").chmod(0o644)
        # Synthetic only: generate an ephemeral Ed25519 signer in RAM,
        # include only the public verification key in the disposable image.
        from importlib.util import spec_from_file_location, module_from_spec
        spec=spec_from_file_location("research_custody",
                SOURCE/"hermes_corpus_custody.py")
        custody=module_from_spec(spec)
        spec.loader.exec_module(custody)
        signer=Ed25519PrivateKey.generate()
        trusted=signer.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        (ctx/"trust.pub").write_bytes(trusted)
        (ctx/"trust.pub").chmod(0o644)
        # Fourth distinct synthetic public verifier; private key stays in RAM.
        executor_signer=Ed25519PrivateKey.generate()
        executor_pub=executor_signer.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        (ctx/"executor.pub").write_bytes(executor_pub)
        (ctx/"executor.pub").chmod(0o644)
        del executor_signer
        raw=(corpus/"decision.md").read_bytes()
        payload={"schema":1,"root":"/research/knowledge","entries":[
            {"path":"decision.md","sha256":hashlib.sha256(raw).hexdigest(),
             "size":len(raw)}]}
        manifest={**payload,"signature":signer.sign(custody.canonical(payload)).hex()}
        # Older epoch is properly signed by the SAME corpus signer, but is
        # intentionally not approved by the image-pinned newer checkpoint.
        stale_raw=b"SYNTHETIC_CONTEXT_OLD\n"
        stale_payload={"schema":1,"root":"/research/knowledge","entries":[
            {"path":"decision.md","sha256":hashlib.sha256(stale_raw).hexdigest(),
             "size":len(stale_raw)}]}
        stale_manifest={**stale_payload,
            "signature":signer.sign(custody.canonical(stale_payload)).hex()}
        manifest_raw=json.dumps(manifest,sort_keys=True)
        stale_manifest_raw=json.dumps(stale_manifest,sort_keys=True)
        (corpus/custody.MANIFEST_NAME).write_text(manifest_raw)
        (corpus/custody.MANIFEST_NAME).chmod(0o644)
        witness=Ed25519PrivateKey.generate()
        witness_pub=witness.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        need(witness_pub!=trusted,"synthetic witness key must be distinct")
        (ctx/"witness.pub").write_bytes(witness_pub)
        (ctx/"witness.pub").chmod(0o644)
        checkpoint_body={
            "schema":1,"generation":2,"root":"/research/knowledge",
            "manifest_sha256":hashlib.sha256(manifest_raw.encode()).hexdigest(),
            "corpus_key_sha256":hashlib.sha256(trusted).hexdigest()}
        checkpoint={**checkpoint_body,
                    "signature":witness.sign(custody.canonical(checkpoint_body)).hex()}
        (ctx/"checkpoint.json").write_text(json.dumps(checkpoint,sort_keys=True))
        (ctx/"checkpoint.json").chmod(0o644)
        del signer, witness
        shutil.copyfile(SITE,work/"sitecustomize.py")
        (work/"sitecustomize.py").chmod(0o644)
        (work/"smoke.py").write_text(prior.SMOKE)
        (work/"smoke.py").chmod(0o644)
        env={"PATH":os.environ.get("PATH","/usr/local/bin:/usr/bin:/bin"),
             "HOME":str(pathlib.Path.home()),
             "HERMES_CANDIDATE_IMAGE":"",
             "HERMES_SYNTHETIC_KNOWLEDGE":str(corpus),
             "HERMES_SYNTHETIC_WORKSPACE":str(work)}
        prefix=["docker","compose","--env-file","/dev/null","--project-name",project,
                "-f",str(COMPOSE),"--profile","synthetic-canary"]
        try:
            for tagname in (base,tag):
                need(cmd(["docker","image","inspect",tagname],7).returncode!=0,
                     "candidate image tag collision")
            need(cmd(["docker","ps","-aq","--filter",
                     "label=com.docker.compose.project="+project],7).stdout.strip()=="",
                 "disposable Compose project already occupied")
            images.append(base)
            need(cmd(["docker","tag",IMAGE,base],10).returncode==0,"cannot pin exact original image")
            images.append(tag)
            bld=cmd(["docker","build","--network=none","--pull=false","--no-cache",
                     "--tag",tag,"--file",str(ctx/"Dockerfile"),str(ctx)],90)
            need(bld.returncode==0,"no-network candidate overlay build failed "+bld.stderr[-850:])
            img=cmd(["docker","image","inspect","-f","{{.Id}}",tag],7)
            need(img.returncode==0 and re.fullmatch(r"sha256:[0-9a-f]{64}",img.stdout.strip()),
                 "unverified candidate image digest")
            checks["exact_published_source_and_entrypoint"]=True
            checks["networkless_offline_overlay_build"]=True
            env["HERMES_CANDIDATE_IMAGE"]=img.stdout.strip()
            rendered=cmd(prefix+["config","--format","json"],20,env=env)
            need(rendered.returncode==0 and len(rendered.stdout)<40000
                 and restricted_render(json.loads(rendered.stdout),
                     img.stdout.strip(),corpus,work),"Compose profile renders unsafe")
            checks["restricted_compose_render_verified"]=True
            create_attempted=True
            cr=cmd(prefix+["create","--no-build","--pull","never",
                           "hermes-split-synthetic"],25,env=env)
            need(cr.returncode==0,"disposable Compose create failed "+cr.stderr[-650:])
            ids=cmd(["docker","ps","-aq","--no-trunc","--filter",
                     "label=com.docker.compose.project="+project],7).stdout.splitlines()
            need(len(ids)==1 and re.fullmatch(r"[a-f0-9]{64}",ids[0]),
                 "unexpected Compose project inventory")
            cid=ids[0]
            inspect_created(cid,img.stdout.strip(),project,corpus,work)
            checks["effective_container_mount_and_security_verified"]=True
            for count in (1,2):
                start=cmd(["docker","start","--attach",cid],45)
                exitinfo=cmd(["docker","inspect","-f","{{.State.ExitCode}}",cid],7)
                need(start.returncode==0 and exitinfo.returncode==0 and
                     exitinfo.stdout.strip()=="0" and
                     start.stdout.splitlines().count("HERMES_SPLIT_NORMAL_ENTRYPOINT_PASS")==1 and
                     f"HERMES_ACTUAL_SPLIT_ROOT_STAGE_{count}_PASS" in start.stdout.splitlines(),
                     f"normal strict executor restart stage {count} failed: "+
                      (start.stdout+start.stderr)[-850:])
            checks["normal_entrypoint_strict_executor_twice"]=True
            checks["kernel_ro_input_rw_scratch_admission_verified"]=True
            state=json.loads((work/"stage.json").read_text())
            reg=json.loads((work/"knowledge"/"eval.json").read_text())
            need(state=={"stage":2,"model_calls":0,"provider_calls":0} and
                 reg["models"]["synthetic/model"]["tasks"]["self:bulk_summarize"]["runs"]==2
                 and (work/"knowledge"/"SUMMARY.md").is_file()
                 and (corpus/"decision.md").read_text()=="SYNTHETIC_CONTEXT_42\n"
                 and not (corpus/"SUMMARY.md").exists()
                 and not list(work.rglob(".env")),"patch did not preserve scratch/source contract")
            checks["actual_patched_selftask_read_write_restart"]=True
            # Replay OLD fully valid signed manifest AND matching old bytes,
            # with the newer witness checkpoint baked in the same image.
            (corpus/"decision.md").write_bytes(stale_raw)
            (corpus/custody.MANIFEST_NAME).write_text(stale_manifest_raw)
            replay=cmd(["docker","start","--attach",cid],35)
            replay_code=cmd(["docker","inspect","-f","{{.State.ExitCode}}",cid],7)
            need(replay_code.returncode==0 and replay_code.stdout.strip()!="0"
                 and "HERMES_SPLIT_NORMAL_ENTRYPOINT_PASS" not in replay.stdout
                 and json.loads((work/"stage.json").read_text())["stage"]==2,
                 "older validly signed corpus was replayed past newer checkpoint")
            checks["older_signed_manifest_replay_denied"]=True
            # Restore the approved signed manifest, then corrupt its bytes.
            (corpus/"decision.md").write_bytes(raw)
            (corpus/custody.MANIFEST_NAME).write_text(manifest_raw)
            # Deliberately modify ONLY the disposable synthetic host corpus.
            # The container bind remains RO, yet its host backing bytes can
            # change: the next fresh adapter start MUST fail before dispatch.
            (corpus/"decision.md").write_text("SYNTHETIC_TAMPERED_BYTES_ONLY\n")
            denied=cmd(["docker","start","--attach",cid],35)
            denied_code=cmd(["docker","inspect","-f","{{.State.ExitCode}}",cid],7)
            need(denied_code.returncode==0 and denied_code.stdout.strip()!="0"
                 and "HERMES_SPLIT_NORMAL_ENTRYPOINT_PASS" not in denied.stdout
                 and "HERMES_ACTUAL_SPLIT_ROOT_STAGE_3_PASS" not in denied.stdout
                 and json.loads((work/"stage.json").read_text())["stage"]==2,
                 "tampered signed corpus was admitted on third startup")
            checks["signed_corpus_mutation_denied_at_relaunch"]=True
            checks["zero_real_tasks_and_providers"]=True
        finally:
            # Clean only disposable project owned by this runner.
            if create_attempted:
                ids=cmd(["docker","ps","-aq","--no-trunc","--filter",
                         "label=com.docker.compose.project="+project],7).stdout.splitlines()
                for owned in ids:
                    if not re.fullmatch(r"[a-f0-9]{64}",owned):
                        continue
                    identified=cmd(["docker","inspect","-f",
                                    "{{json .Config.Labels}}",owned],7)
                    if identified.returncode != 0:
                        continue
                    labels=json.loads(identified.stdout)
                    if (labels.get("com.docker.compose.project") == project and
                        labels.get("com.docker.compose.service") ==
                        "hermes-split-synthetic"):
                        cmd(["docker","rm","--force",owned],15)
            for name in reversed(images):
                cmd(["docker","image","rm",name],15)
            remains=cmd(["docker","ps","-aq","--filter",
                         "label=com.docker.compose.project="+project],7)
            checks["all_disposable_docker_objects_removed"]=(remains.returncode==0 and
                not remains.stdout.strip() and all(
                cmd(["docker","image","inspect",name],7).returncode!=0
                for name in (base,tag)))
    live=cmd(["docker","inspect","-f","{{.Image}} {{.State.Running}}",LIVE],7)
    checks["production_hermes_running_unchanged"]=(
        live.returncode==0 and live.stdout.strip()==before)
    need(all(checks.values()),"verification incomplete: "+str(
        [k for k,v in checks.items() if not v]))
    return {"decision":"HOLD","checks":checks,
            "real_agent_tasks_executed":0,"provider_requests_forwarded":0,
            "production_deployment_authorized":False,"blockers":list(BLOCKERS)}

if __name__=="__main__":
    arg=argparse.ArgumentParser()
    arg.add_argument("--physical-disposable-compose",action="store_true")
    arg.add_argument("--json",action="store_true")
    args=arg.parse_args()
    data=(run() if args.physical_disposable_compose else
          {"decision":"HOLD","physical_executed":False,
           "production_deployment_authorized":False,"blockers":list(BLOCKERS)})
    print(json.dumps(data,sort_keys=True,indent=2 if args.json else None))
