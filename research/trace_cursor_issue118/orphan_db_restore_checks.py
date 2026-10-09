"""Read-only bounded SQLite integrity checks for physically embedded orphan DB members."""
import collections
import hashlib
import json
import pathlib
import sqlite3
import subprocess

ROOT = pathlib.Path("/media/scott/SSD_4TB/finetune-trace-spool/sealed")
MAX_DB_BYTES = 48 * 1024 * 1024

def extract(archive, name):
    if not isinstance(name,str) or not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("invalid internal member name")
    p = subprocess.Popen(["timeout","30","tar","-I","zstd","-xOf",str(archive),"./"+name],
                         stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        data = p.stdout.read(MAX_DB_BYTES + 1)
        if len(data) > MAX_DB_BYTES:
            raise ValueError("member exceeds bounded budget")
        if p.wait(timeout=34)!=0:
            raise ValueError("archive extraction error")
        return data
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=3)
        p.stdout.close()

def main():
    manifest_archives={p.name[:-len(".manifest.json")] for p in ROOT.glob("*.tar.zst.manifest.json")}
    counts=collections.Counter()
    rows=[]
    for archive in sorted(ROOT.glob("*.tar.zst")):
        if archive.name in manifest_archives:
            continue
        manifest=json.loads(extract(archive,"manifest.json"))
        member_listing=subprocess.run(["timeout","20","tar","-I","zstd","-tf",str(archive)],
                                      capture_output=True,timeout=24,check=True)
        members=set(member_listing.stdout.decode().splitlines())
        for item in manifest.get("databases",[]):
            basename=item.get("basename")
            if not isinstance(basename,str):
                raise ValueError("missing database basename")
            if "./"+basename not in members:
                counts["reference_only"]+=1
                rows.append({"kind":"reference_only","has_reference":bool(item.get("reference_segment")),
                             "has_digest":bool(item.get("sha256"))})
                continue
            counts["embedded"]+=1
            content=extract(archive,basename)
            size_ok=len(content)==item.get("stored_bytes")
            digest_ok=hashlib.sha256(content).hexdigest()==item.get("sha256")
            con=sqlite3.connect(":memory:")
            try:
                con.deserialize(content)
                reply=con.execute("PRAGMA quick_check").fetchone()
                integrity_ok=bool(reply) and reply[0]=="ok"
            except (sqlite3.Error,ValueError):
                integrity_ok=False
            finally:
                con.close()
            counts["size_pass"]+=int(size_ok)
            counts["sha256_pass"]+=int(digest_ok)
            counts["sqlite_quick_check_pass"]+=int(integrity_ok)
            rows.append({"kind":"embedded","bytes_checked":len(content),
                         "size_verified":size_ok,"sha256_verified":digest_ok,
                         "sqlite_quick_check_ok":integrity_ok})
    result={"read_only":True,"sqlite_bytes_persisted":0,
        "archive_files_mutated":0,"raw_sqlite_content_printed":False,
        "counts":dict(counts),"records":rows}
    target=pathlib.Path("/home/scott/git/delm-sandbox/cursor-final-20261009/orphan_db_restore_checks_20261009.json")
    target.write_text(json.dumps(result,indent=2))
    print("EMBEDDED_ORPHAN_DB_ACCEPTANCE",json.dumps(result))
if __name__=="__main__":
    main()

