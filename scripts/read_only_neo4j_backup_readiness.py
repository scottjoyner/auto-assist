#!/usr/bin/env python3
"""Read-only image-pinned native backup listener and recovery RPO evidence.

This tool NEVER runs a backup, copies database files, changes Docker, or opens
new inbound ports. Even complete local evidence returns HOLD (exit code 3).
No raw credentials, host addresses, mount sources, or graph data are emitted.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any


IMAGE_RE = re.compile(r"sha256:[a-f0-9]{64}\Z")
LISTENER_PORT = 6362
GRAPH_CONTAINER = "neo4j"
API_CONTAINER = "assistx-api"

# Fixed query; credentials are read only inside the already-running API image
# and never passed to the host shell, this script, or CI.
_NATIVE_BACKUP_SETTINGS_QUERY = '''
import os,json,ipaddress
from neo4j import GraphDatabase,READ_ACCESS,Query
d=GraphDatabase.driver(os.environ["NEO4J_URI"],auth=(os.environ["NEO4J_USER"],os.environ["NEO4J_PASSWORD"]))
try:
 with d.session(database="system",default_access_mode=READ_ACCESS) as s:
  rows={r["name"]:str(r["value"] or "") for r in s.run(Query(
    "SHOW SETTINGS YIELD name,value WHERE name IN ['server.backup.enabled','server.backup.listen_address'] RETURN name,value",timeout=6))}
  v=rows.get("server.backup.listen_address","")
  h=v.rsplit(":",1)[0].strip("[]")
  if h in ("","*","0.0.0.0","::"):scope="wildcard"
  else:
   try:scope="loopback" if ipaddress.ip_address(h).is_loopback else "nonloopback"
   except ValueError:scope="unverified"
  print(json.dumps({"enabled":rows.get("server.backup.enabled","").lower()=="true",
   "listen_scope":scope,"listen_port_6362":v.endswith(":6362"),
   "expected_settings_present":len(rows)==2},sort_keys=True))
finally:d.close()
'''


class BackupHold(RuntimeError):
    """Insufficient trust in an image-pinned, immutable native backup snapshot."""


def _call(command: list[str], timeout: int = 9, max_bytes: int = 256000) -> str:
    try:
        r = subprocess.run(command, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupHold("bounded_native_read_unavailable") from exc
    if r.returncode != 0 or len(r.stdout)>max_bytes:
        raise BackupHold("bounded_native_read_failed")
    return r.stdout


def _inspect(name: str, expected_image: str) -> dict[str, Any]:
    if name not in (GRAPH_CONTAINER,API_CONTAINER):
        raise BackupHold("untrusted_container_name")
    if not isinstance(expected_image,str) or not IMAGE_RE.fullmatch(expected_image):
        raise BackupHold("immutable_image_digest_required")
    raw=_call(["docker","inspect",name])
    try:
        objs=json.loads(raw)
    except (ValueError,TypeError) as exc:
        raise BackupHold("docker_json_untrusted") from exc
    if not isinstance(objs,list) or len(objs)!=1 or not isinstance(objs[0],dict):
        raise BackupHold("docker_identity_ambiguous")
    obj=objs[0]
    if (obj.get("Name")!="/"+name or obj.get("Image")!=expected_image or
        (obj.get("State") or {}).get("Running") is not True):
        raise BackupHold("runtime_image_or_state_changed")
    cid=obj.get("Id")
    started=(obj.get("State") or {}).get("StartedAt")
    if (not isinstance(cid,str) or not re.fullmatch(r"[0-9a-f]{64}",cid)
        or not isinstance(started,str) or not started):
        raise BackupHold("runtime_identity_invalid")
    return obj


def _identity(o: dict[str,Any]) -> tuple[str,str]:
    return o["Id"],o["State"]["StartedAt"]


def _decode_listening_hex(h: str, family: str) -> str:
    try:
        b=bytes.fromhex(h)
        if family=="tcp" and len(b)==4:
            ip=ipaddress.IPv4Address(b[::-1])
        elif family=="tcp6" and len(b)==16:
            ip=ipaddress.IPv6Address(b"".join(b[i:i+4][::-1] for i in (0,4,8,12)))
            if ip.ipv4_mapped:
                ip=ip.ipv4_mapped
        else:
            raise ValueError("invalid_address_shape")
    except (ValueError,TypeError) as exc:
        raise BackupHold("kernel_socket_address_invalid") from exc
    return "loopback" if ip.is_loopback else "wildcard" if ip.is_unspecified else "nonloopback"


def _kernel_listeners(tcp: str, tcp6: str) -> list[str]:
    listeners=[]
    for family,src in (("tcp",tcp),("tcp6",tcp6)):
        if not isinstance(src,str) or len(src)>100000:
            raise BackupHold("kernel_socket_source_untrusted")
        for raw in src.splitlines()[1:]:
            p=raw.split()
            if len(p)<4 or ":" not in p[1]:
                continue
            try:
                port=int(p[1].split(":")[-1],16)
            except ValueError:
                continue
            if port!=LISTENER_PORT or p[3]!="0A":
                continue
            listeners.append(_decode_listening_hex(p[1].split(":")[0],family))
    return listeners


def _archive_metadata(data: Path, now: float, max_age_hours: int) -> dict[str,Any]:
    # Metadata-only, bounded to two levels, never read backup file contents.
    if not data.is_dir() or data.is_symlink():
        raise BackupHold("live_graph_bind_mount_unverified")
    seen=[]
    scanned=0
    for root,dirs,files in os.walk(data,followlinks=False):
        depth=len(Path(root).relative_to(data).parts)
        if depth>=2:
            dirs.clear()
        elif len(dirs)>30:
            raise BackupHold("archive_subdirectory_inventory_incomplete")
        if len(files)>500:
            raise BackupHold("archive_file_inventory_incomplete")
        for name in files:
            scanned+=1
            if not name.lower().endswith((".dump",".backup")):
                continue
            path=Path(root)/name
            if path.is_symlink():
                raise BackupHold("archived_file_is_symlink")
            st=path.stat()
            if not stat.S_ISREG(st.st_mode) or st.st_mtime>now+60:
                raise BackupHold("archived_file_type_or_time_untrusted")
            typ="dump" if name.lower().endswith(".dump") else "backup"
            seen.append((typ,max(0,now-st.st_mtime)))
    return {
        "bounded_files_scanned":scanned,
        "historical_dump_count":sum(k=="dump" for k,_ in seen),
        "backup_chain_file_count":sum(k=="backup" for k,_ in seen),
        "fresh_archive_count_in_data_mount":sum(age<=max_age_hours*3600 for _,age in seen),
        "oldest_archive_age_days":round(max((age for _,age in seen),default=0)/86400,1),
        "archive_bytes_not_read":True,
        "outside_graph_data_mount_backups_verified":False,
    }


def _published_backup_ports(o: dict[str,Any]) -> int:
    cfg=(o.get("HostConfig") or {}).get("PortBindings")
    if not isinstance(cfg,dict):
        raise BackupHold("host_port_bindings_unverified")
    count=0
    for key, rows in cfg.items():
        if not isinstance(key,str):
            raise BackupHold("host_port_bindings_untrusted")
        if key.startswith(str(LISTENER_PORT)+"/"):
            if rows is not None and not isinstance(rows,list):
                raise BackupHold("backup_host_port_bindings_malformed")
            count+=len(rows or [])
    return count


def assess(graph: dict[str,Any], api: dict[str,Any],
           settings: dict[str,Any], tcp: str, tcp6: str,
           *, now: float, max_age_hours: int) -> dict[str,Any]:
    if settings.get("expected_settings_present") is not True:
        raise BackupHold("effective_native_settings_unverified")
    mounts=[m for m in (graph.get("Mounts") or []) if m.get("Destination")=="/data"]
    if (len(mounts)!=1 or mounts[0].get("Type")!="bind" or
        not isinstance(mounts[0].get("Source"),str)):
        raise BackupHold("neo4j_live_data_mount_unverified")
    data=Path(mounts[0]["Source"])
    if data.is_symlink() or not data.is_dir():
        raise BackupHold("neo4j_live_data_path_unverified")
    stats=os.statvfs(data)
    free_gib=round(stats.f_bavail*stats.f_frsize/(2**30),1)
    archives=_archive_metadata(data,now,max_age_hours)
    sockets=_kernel_listeners(tcp,tcp6)
    all_loopback=bool(sockets) and all(x=="loopback" for x in sockets)
    published=_published_backup_ports(graph)
    risks=[
        "backup_client_access_path_requires_scoped_namespace",
        "fresh_backup_restore_and_transaction_coverage_unverified",
        "off_host_immutable_custody_and_recovery_approval_unverified",
    ]
    if not settings.get("enabled"):
        risks.append("backup_server_disabled")
    if settings.get("listen_scope")!="loopback":
        risks.append("backup_listener_setting_not_loopback")
    if not all_loopback or not settings.get("listen_port_6362"):
        risks.append("kernel_backup_listener_does_not_match_loopback_policy")
    if published:
        risks.append("backup_service_host_published")
    if not archives["fresh_archive_count_in_data_mount"]:
        risks.append("no_fresh_archive_in_scoped_graph_data_inventory")
    if free_gib<30:
        risks.append("insufficient_scratch_space_for_recovery_canary")
    return {
        "production_decision":"HOLD", "backup_run_authorized":False,
        "production_cutover_authorized":False,"dispatch_authorized":False,
        "evidence_mode":"IMAGE_PINNED_READ_ONLY_BACKUP_PREFLIGHT",
        "native_backup_enabled":settings["enabled"],
        "native_backup_listen_setting_loopback":settings["listen_scope"]=="loopback",
        "actual_backup_kernel_listeners":len(sockets),
        "actual_backup_listeners_all_loopback":all_loopback,
        "host_published_backup_bindings":published,
        "data_mount_free_gib":free_gib,
        "archive_inventory":archives,
        "fresh_backup_restorability_verified":False,
        "network_reachability_verified":False,
        "backup_protocol_authorization_verified":False,
        "approved_backup_destination_verified":False,
        "risk_flags":sorted(set(risks)),
        "no_production_writes":True,
    }


def collect(graph_image: str, api_image: str, max_age_hours: int=24) -> dict[str,Any]:
    if type(max_age_hours) is not int or not 1<=max_age_hours<=168:
        raise BackupHold("rpo_bound_invalid")
    graph=_inspect(GRAPH_CONTAINER,graph_image)
    api=_inspect(API_CONTAINER,api_image)
    native=_call(["docker","exec","-i",API_CONTAINER,"python3","-c",
                 _NATIVE_BACKUP_SETTINGS_QUERY],timeout=16,max_bytes=2000)
    try:
        settings=json.loads(native.strip())
    except ValueError as exc:
        raise BackupHold("native_backup_settings_json_untrusted") from exc
    if not isinstance(settings,dict):
        raise BackupHold("native_backup_settings_shape_untrusted")
    tcp=_call(["docker","exec",GRAPH_CONTAINER,"cat","/proc/net/tcp"],max_bytes=100000)
    tcp6=_call(["docker","exec",GRAPH_CONTAINER,"cat","/proc/net/tcp6"],max_bytes=100000)
    result=assess(graph,api,settings,tcp,tcp6,now=time.time(),
                  max_age_hours=max_age_hours)
    after_graph=_inspect(GRAPH_CONTAINER,graph_image)
    after_api=_inspect(API_CONTAINER,api_image)
    if (_identity(graph)!=_identity(after_graph) or _identity(api)!=_identity(after_api)
        or _published_backup_ports(graph)!=_published_backup_ports(after_graph)
        or graph.get("Mounts")!=after_graph.get("Mounts")):
        raise BackupHold("production_container_changed_during_readback")
    return result


def main(argv: list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-graph-image",required=True)
    parser.add_argument("--expected-api-image",required=True)
    parser.add_argument("--max-age-hours",type=int,default=24)
    parser.add_argument("--json",action="store_true",required=True)
    args=parser.parse_args(argv)
    try:
        result=collect(args.expected_graph_image,args.expected_api_image,args.max_age_hours)
    except Exception:
        print(json.dumps({"production_decision":"HOLD","backup_run_authorized":False,
          "production_cutover_authorized":False,"error_type":"read_only_backup_preflight_unverified"}))
        return 3
    print(json.dumps(result,sort_keys=True))
    return 3


if __name__=="__main__":
    sys.exit(main())
