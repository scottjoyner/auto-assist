"""Issue #118 read-only database content resolution from local sealed archive custody.

Content-addressed recoverability is not a source-generation / historical-lineage witness.
Never writes a live archive, sidecar, checkpoint, or temporary SQLite database.
"""
from __future__ import annotations
from collections import Counter,defaultdict
import hashlib
import json
import pathlib
import re
import sqlite3
import subprocess
from orphan_db_restore_checks import extract

ROOT=pathlib.Path("/media/scott/SSD_4TB/finetune-trace-spool/sealed")
_HEX=re.compile(r"[0-9a-f]{64}\Z")
MAX_MANIFEST_BYTES=4*1024*1024

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def load_manifest(path: pathlib.Path):
    if path.stat().st_size>MAX_MANIFEST_BYTES:
        raise ValueError("oversized sidecar")
    data=json.loads(path.read_bytes())
    if type(data) is not dict or type(data.get("databases",[])) is not list:
        raise ValueError("invalid manifest schema")
    return data

def check_physical(archive: pathlib.Path, item: dict) -> dict:
    name=item.get("basename")
    expected=item.get("sha256")
    size=item.get("stored_bytes")
    if type(name) is not str or type(expected) is not str or _HEX.fullmatch(expected) is None:
        raise ValueError("invalid physical DB receipt")
    if type(size) is not int or size<0:
        raise ValueError("invalid stored DB size")
    data=extract(archive,name)
    length_match=len(data)==size
    hash_match=digest(data)==expected
    valid_db=False
    if length_match and hash_match and data.startswith(b"SQLite format 3"):
        connection=sqlite3.connect(":memory:")
        try:
            connection.deserialize(data)
            operations=[0]
            def bounded_progress():
                operations[0]+=1
                return 1 if operations[0]>20 else 0
            connection.set_progress_handler(bounded_progress,1_000_000)
            try:
                result=connection.execute("PRAGMA quick_check").fetchone()
                valid_db=bool(result) and result[0]=="ok"
            finally:
                connection.set_progress_handler(None,0)
        except sqlite3.Error:
            valid_db=False
        finally:
            connection.close()
    return {"size_match":length_match,"sha256_match":hash_match,
            "quick_check":valid_db,"bytes_verified":len(data)}

def run(root=ROOT):
    archives={path.name:path for path in root.glob("*.tar.zst")}
    with_receipt={p.name[:-len(".manifest.json")]:p
                  for p in root.glob("*.tar.zst.manifest.json")}
    orphan=[p for name,p in archives.items() if name not in with_receipt]
    if not orphan:
        raise ValueError("no orphan archives under test")
    orphan_rows=[]
    physical=defaultdict(list)
    by_archive={}
    for archive in orphan:
        obj=json.loads(extract(archive,"manifest.json"))
        if type(obj) is not dict or type(obj.get("databases")) is not list:
            raise ValueError("bad internal database receipt")
        by_archive[archive.name]=obj
        for item in obj["databases"]:
            sha=item.get("sha256")
            if type(sha) is not str or _HEX.fullmatch(sha) is None:
                raise ValueError("invalid DB hash metadata")
            if item.get("reference_segment"):
                orphan_rows.append({"kind":"reference","sha":sha,
                                    "original_ref":item["reference_segment"]})
            else:
                orphan_rows.append({"kind":"embedded","sha":sha})
                physical[sha].append((archive,item))
    for archive_name,sidecar in with_receipt.items():
        if archive_name not in archives:
            continue
        obj=load_manifest(sidecar)
        for item in obj.get("databases",[]):
            sha=item.get("sha256")
            if type(sha) is str and _HEX.fullmatch(sha) and not item.get("reference_segment"):
                physical[sha].append((archives[archive_name],item))
    counts=Counter()
    proof={}
    for sha in sorted(set(row["sha"] for row in orphan_rows)):
        candidates=physical.get(sha,[])[:8]
        counts["unique_needed_digests"]+=1
        success=False
        for archive,item in candidates:
            check=check_physical(archive,item)
            counts["physical_candidate_checks"]+=1
            if all(check[x] for x in ("size_match","sha256_match","quick_check")):
                proof[sha]=True
                counts["physical_unique_digests_verified"]+=1
                counts["physical_unique_bytes_verified"]+=check["bytes_verified"]
                success=True
                break
        if not success:
            proof[sha]=False
            counts["physical_unique_digests_missing"]+=1
    for row in orphan_rows:
        counts[row["kind"]+"_records"]+=1
        counts["all_records_content_restorable"]+=int(proof.get(row["sha"],False))
        if row["kind"]=="reference":
            ref=row["original_ref"]
            matching=[name for name in archives if type(ref) is str and ref in name]
            if len(matching)==1:
                counts["reference_unique_name_pointer"]+=1
            else:
                counts["reference_unresolved_name_pointer"]+=1
    result={"read_only":True,"raw_database_contents_reported":False,
            "source_checkpoints_modified":0,"sidecars_created":0,
            "orphan_archives":len(orphan),"counts":dict(counts),
            "all_content_restorable":counts["all_records_content_restorable"]==len(orphan_rows),
            "full_reference_chain_proven":False}
    return result

if __name__=="__main__":
    print("ORPHAN_CONTENT_RECOVERY_WITNESS",json.dumps(run(),sort_keys=True))

