#!/usr/bin/env python3
"""Metadata-only production spool acceptance; never opens trace payloads or tar members."""
import json
import os
import pathlib
import stat
import time
from collections import Counter

SPOOL = pathlib.Path("/media/scott/SSD_4TB/finetune-trace-spool")
REPORT = pathlib.Path("/home/scott/git/delm-sandbox/cursor-final-20261009/metadata-preflight-20261009.json")
MAX_SIDECAR_BYTES = 4*1024*1024
def bounded_json(p):
    with p.open("rb") as f:
        b=f.read(MAX_SIDECAR_BYTES+1)
    if len(b)>MAX_SIDECAR_BYTES:raise ValueError("oversized_metadata")
    return json.loads(b)
def main():
    t=time.monotonic()
    assert not SPOOL.is_symlink()
    for n in ("state","sealed"):
        assert (SPOOL/n).is_dir() and not (SPOOL/n).is_symlink()
    source_types=Counter(); receipt_types=Counter(); anomalies=Counter()
    cursor=SPOOL/"state"/"offsets.json"
    if cursor.exists():
        try:
            obj=bounded_json(cursor)
            src=obj.get("sources",{})
            if not isinstance(src,dict): raise ValueError("sources_not_object")
            for state in src.values():
                if not isinstance(state,dict):
                    source_types["not_object"]+=1;continue
                keys=state.keys()
                if all(k in keys for k in ("source_dev","source_inode","prefix_sha256","offset")):
                    source_types["provenance_complete"]+=1
                elif "offset" in keys:source_types["legacy_offset_only"]+=1
                else:source_types["missing_offset"]+=1
            cursor_read="OK"
        except (OSError,ValueError,TypeError) as exc:
            cursor_read=type(exc).__name__;anomalies["invalid_state_file"]+=1
    else:
        cursor_read="MISSING";anomalies["missing_state_file"]+=1
    archived={}
    manifest={}
    ready={}
    t_scan=time.monotonic()
    with os.scandir(SPOOL/"sealed") as entries:
        for e in entries:
            name=e.name
            if not e.is_file(follow_symlinks=False):continue
            if name.endswith('.tar.zst'): archived[name]=e.stat().st_size
            elif name.endswith('.manifest.json'): manifest[name[:-len('.manifest.json')]]=e.stat().st_size
            elif name.endswith('.ready'): ready[name[:-len('.ready')]]=e.stat().st_size
    t_index=time.monotonic()
    for n in archived.keys()|manifest.keys()|ready.keys():
        if not (n in archived and n in manifest and n in ready):
            anomalies['incomplete_triple']+=1
    for name,size in manifest.items():
        if size>MAX_SIDECAR_BYTES:
            anomalies['oversized_manifest']+=1
            continue
        try:
            obj=bounded_json(SPOOL/'sealed'/(name+'.manifest.json'))
            items=obj.get('files',[])
            if not isinstance(items,list):raise ValueError('invalid_files')
            for item in items:
                if not isinstance(item,dict):
                    receipt_types['malformed']+=1
                elif all(k in item for k in ('offset_end_committed','source_dev','source_inode','prefix_sha256')):
                    receipt_types['provenance_complete']+=1
                elif 'offset_end_committed' in item:
                    receipt_types['partial']+=1
                else:
                    receipt_types['legacy']+=1
        except (OSError,ValueError,TypeError):
            anomalies['invalid_manifest']+=1
    result={'read_only':True,'trace_payloads_opened':0,
       'cursor_state':cursor_read,'cursor_categories':dict(source_types),
       'archives':len(archived),'manifests':len(manifest),'ready':len(ready),
       'receipt_categories':dict(receipt_types),'anomalies':dict(anomalies),
       'metadata_scan_seconds':round(time.monotonic()-t_index,3),
       'sealed_index_seconds':round(t_index-t_scan,3),
       'total_seconds':round(time.monotonic()-t,3)}
    result['migration']='HOLD' if source_types['legacy_offset_only'] or receipt_types['legacy'] or anomalies else 'REVIEW'
    REPORT.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()