"""One-pass local sealed archive index; metadata-only, deny on ambiguity."""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

_HEX=re.compile(r"[0-9a-f]{64}\Z")
_LIMIT=4*1024*1024

def build_seal_index(sealed: Path) -> dict:
    archives=set()
    manifests=set()
    ready=set()
    errors=[]
    groups=defaultdict(list)
    for entry in os.scandir(sealed):
        name=entry.name
        kind=None
        if name.endswith(".tar.zst.manifest.json"):
            kind="manifest";base=name[:-len(".manifest.json")]
        elif name.endswith(".tar.zst.ready"):
            kind="ready";base=name[:-len(".ready")]
        elif name.endswith(".tar.zst"):
            kind="archive";base=name
        else:
            continue
        if not entry.is_file(follow_symlinks=False):
            errors.append("invalid sealed object type")
            continue
        {"archive":archives,"manifest":manifests,"ready":ready}[kind].add(base)
    for base in sorted(archives|manifests|ready):
        if base not in archives or base not in manifests or base not in ready:
            errors.append("incomplete local seal")
            continue
        path=sealed/(base+".manifest.json")
        try:
            if path.stat().st_size>_LIMIT:
                raise ValueError("oversized_manifest")
            with path.open("rb") as f:
                data=f.read(_LIMIT+1)
            if len(data)>_LIMIT:raise ValueError("oversized_manifest")
            m=json.loads(data)
            if not isinstance(m,dict) or m.get("archive")!=base:
                raise ValueError("invalid_manifest")
            digest=m.get("archive_sha256")
            if not isinstance(digest,str) or _HEX.fullmatch(digest) is None:
                raise ValueError("invalid_archive_digest")
            rp=sealed/(base+".ready")
            if rp.stat().st_size>128:
                raise ValueError("oversized_ready")
            if rp.read_text(encoding="utf-8").strip()!=digest:
                raise ValueError("ready_mismatch")
            rows=m.get("files")
            if not isinstance(rows,list):
                raise ValueError("missing_manifest_files")
            for row in rows:
                if not isinstance(row,dict):
                    raise ValueError("malformed_row")
                source=row.get("source");group=row.get("source_group")
                if not isinstance(source,str) or not isinstance(group,str):
                    raise ValueError("invalid_source_path")
                end=row.get("offset_end_committed")
                if type(end) is not int or end<0:end=None
                groups[(source,group)].append((base,end))
        except (OSError,ValueError,TypeError):
            errors.append("invalid local seal receipt")
    return {"errors":errors,"groups":dict(groups),
            "archives":len(archives),"manifests":len(manifests),
            "ready":len(ready)}

def check_seal_index(index: dict, source: Path, group: str, committed: int) -> list[str]:
    if index["errors"]:
        return index["errors"][:3]
    failures=[]
    for _base,end in index["groups"].get((str(source),group),[]):
        if end is None:
            failures.append("legacy seal missing committed boundary")
        elif end>committed:
            failures.append("sealed interval newer than checkpoint")
    return failures