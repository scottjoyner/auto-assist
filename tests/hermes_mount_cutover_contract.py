"""Read-only fail-closed Hermes legacy mount cutover contract; no deployment."""
import argparse
import json
import os
import subprocess

IMAGE = "sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"
REQUIRED = {"/app/src", "/root/knowledge", "/media/scott/NAS5",
            "/media/scott/SSD_4TB", "/root/.ssh/id_hermes",
            "/root/.ssh/config", "/root/.ssh/id_hermes.pub",
            "/home/scott/git/auto-assist", "/home/scott/git/auto-router"}
BLOCKERS = ["live_legacy_mounts_not_replaced", "approved_signed_ro_corpus_missing",
            "approved_isolated_rw_scratch_missing", "independent_witness_custody_missing",
            "owner_bound_receipts_fencing_and_revocation_missing",
            "exact_image_rollback_and_recovery_not_approved"]

class CutoverDenied(ValueError):
    pass

def need(ok, message):
    if not ok:
        raise CutoverDenied("Hermes cutover: " + message)

def classify(target):
    if target.startswith("/root/.ssh/"):
        return "remove_private_ssh"
    if target in ("/root/knowledge", "/media/scott/SSD_4TB"):
        return "remove_protected_storage"
    if target == "/media/scott/NAS5":
        return "remove_unverified_nas_label"
    if target == "/app/src" or target.startswith("/home/scott/git/"):
        return "remove_mutable_code"
    return "remove_unreviewed_legacy_mount"

def plan_from_inspect(mounts, image, running):
    need(type(mounts) is list and len(mounts) == 16, "changed inventory")
    need(image == IMAGE and running is True, "unverified image or state")
    targets = set()
    removal = []
    for m in mounts:
        need(type(m) is dict, "malformed mount")
        dst, src = m.get("Destination"), m.get("Source")
        need(type(dst) is str and dst.startswith("/") and dst != "/"
             and ".." not in dst.split("/") and "//" not in dst
             and dst not in targets, "invalid or duplicate destination")
        need(m.get("Type") == "bind" and type(m.get("RW")) is bool
             and type(src) is str and src.startswith("/"), "unknown mount mode")
        targets.add(dst)
        removal.append({"target": dst, "writable": m["RW"],
                        "action": classify(dst)})
    need(REQUIRED.issubset(targets), "sensitive legacy mount changed")
    need(not targets.intersection({"/research/knowledge", "/workspace"}),
         "candidate mount overlaps legacy")
    return {
        "decision": "HOLD", "schema": 1, "production_modified": False,
        "production_deployment_authorized": False,
        "remove_legacy_mounts": sorted(removal, key=lambda x:x["target"]),
        "remove_legacy_count": len(removal),
        "live_writable_count": sum(m["writable"] for m in removal),
        "replacement_mounts": [
            {"target": "/research/knowledge", "readonly": True,
             "source": "APPROVED_SIGNED_CORPUS_REQUIRED"},
            {"target": "/workspace", "readonly": False,
             "source": "SEPARATELY_AUTHORIZED_SCRATCH_REQUIRED"}],
        "release_blockers": BLOCKERS,
    }

def physical_readonly():
    need(os.uname().nodename == "x1-370" and os.geteuid() == 1000,
         "authorized x1-370 nonroot only")
    mounts = subprocess.run(["docker", "inspect", "--format",
                             "{{json .Mounts}}", "assistx-hermes-adapter"],
                            capture_output=True, text=True, timeout=12)
    status = subprocess.run(["docker", "inspect", "--format",
                             "{{.Image}} {{.State.Running}}", "assistx-hermes-adapter"],
                            capture_output=True, text=True, timeout=12)
    need(mounts.returncode == status.returncode == 0
         and len(mounts.stdout) < 150000
         and len(status.stdout) < 256, "bounded Docker metadata unavailable")
    fields = status.stdout.strip().split()
    need(len(fields) == 2, "bad image status")
    return plan_from_inspect(json.loads(mounts.stdout), fields[0], fields[1] == "true")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--physical-readonly-cutover", action="store_true")
    args = parser.parse_args()
    result = physical_readonly() if args.physical_readonly_cutover else {
        "decision":"HOLD", "physical_executed":False,
        "production_deployment_authorized":False}
    print(json.dumps(result,sort_keys=True))
