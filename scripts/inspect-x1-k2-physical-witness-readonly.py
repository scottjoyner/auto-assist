#!/usr/bin/env python3
"""Non-admitting x1 LM Studio process/socket/model witness.

READ ONLY. Fails closed on opaque /proc access, PID/exe ambiguity, disconnected
model inventory or absent independent signature. It never supplies approval
credentials, issues tokens, loads models or updates Neo4j.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PROC = Path("/proc")
EXECUTABLE = Path("/opt/LM-Studio/lm-studio")
PROXY_EXECUTABLE = Path("/usr/bin/socat")
PROXY_IPS = {"lan": "192.168.1.237", "tailscale": "100.64.43.123"}
PACKAGE = Path("/opt/LM-Studio/resources/app/package.json")
MODEL = Path("/home/scott/.lmstudio/models/IFM/K2-Horizon-0.9B-GGUF/K2-Horizon-1B-BF16.gguf")
EXPECTED_MODEL_SHA = "371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8"
EXPECTED_MODEL_BYTES = 2_159_424_896
REVIEWED_VERSION = "0.4.26+4"
MODEL_ID = "k2-horizon-0.9b"
PORT = 1234
PATHS = {
    "loopback": "http://127.0.0.1:1234/v1",
    "lan": "http://192.168.1.237:1234/v1",
    "tailscale": "http://100.64.43.123:1234/v1",
}
MAX_API_BYTES = 65536
MAX_PROCESSES = 4000
MAX_DESCENDANTS = 48
SOCKET = re.compile(r"socket:\[(\d+)\]\Z")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect")


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def proc_stat(path: Path) -> dict:
    # The comm field may contain spaces and ')' characters: don't use split
    # on the entire /proc/<pid>/stat.
    raw = path.read_text(encoding="ascii")
    sep = raw.rfind(") ")
    if sep < 1:
        raise ValueError("invalid_proc_stat")
    values = raw[sep + 2:].split()
    if len(values) < 20:
        raise ValueError("truncated_proc_stat")
    return {"ppid": int(values[1]), "start_ticks": int(values[19])}


def proc_cmdline(path: Path) -> list[str]:
    raw = path.read_bytes()
    if not raw or len(raw) > 32768:
        raise ValueError("invalid_proc_command_line")
    return [s.decode("utf-8", "replace") for s in raw.rstrip(b"\x00").split(b"\x00")]


def process_tree(proc_root: Path) -> dict:
    processes: dict[int, dict] = {}
    if not proc_root.is_dir():
        raise ValueError("proc_root_missing")
    count = 0
    for entry in proc_root.iterdir():
        if not entry.name.isdecimal():
            continue
        count += 1
        if count > MAX_PROCESSES:
            raise ValueError("proc_count_exceeds_cap")
        pid = int(entry.name)
        try:
            stat = proc_stat(entry / "stat")
            cmd = proc_cmdline(entry / "cmdline")
            if cmd:
                processes[pid] = {**stat, "cmd": cmd}
        except (OSError, ValueError):
            continue
    candidates = [
        pid for pid, item in processes.items()
        if item["cmd"] == [str(EXECUTABLE), "--run-as-service"]
    ]
    if len(candidates) != 1:
        raise ValueError("runtime_process_missing_or_ambiguous")
    root_pid = candidates[0]
    descendants = {root_pid}
    # Bounded closure; no process launch, signal or mutation.
    for _ in range(MAX_DESCENDANTS):
        newer = {pid for pid, item in processes.items() if item["ppid"] in descendants}
        merged = descendants | newer
        if len(merged) > MAX_DESCENDANTS:
            raise ValueError("runtime_process_tree_exceeds_cap")
        if merged == descendants:
            break
        descendants = merged
    # The private LAN/Tailscale ingress points are separate systemd
    # socat proxy services, not descendants of the LM Studio process.
    # Require their exact loopback forwarding commands and unique PIDs.
    proxies = {}
    for kind, bind_ip in PROXY_IPS.items():
        expected = [
            str(PROXY_EXECUTABLE),
            f"TCP-LISTEN:{PORT},bind={bind_ip},fork,reuseaddr",
            f"TCP:127.0.0.1:{PORT}",
        ]
        found = [pid for pid, item in processes.items()
                 if item["cmd"] == expected]
        if len(found) != 1:
            raise ValueError("transport_proxy_process_missing_or_ambiguous")
        proxy_pid = found[0]
        proxies[kind] = {
            "pid": proxy_pid,
            "start_ticks": processes[proxy_pid]["start_ticks"],
        }
    return {"root_pid": root_pid,
            "start_ticks": processes[root_pid]["start_ticks"],
            "descendants": sorted(descendants),
            "proxies": proxies}


def listener_inodes(proc_root: Path) -> dict:
    found: dict[str, set[int]] = {}
    for name in ("tcp", "tcp6"):
        path = proc_root / "net" / name
        try:
            rows = path.read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            raise ValueError("tcp_table_unreadable")
        if len(rows) > 100000:
            raise ValueError("tcp_table_exceeds_cap")
        for row in rows:
            fields = row.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            try:
                address, hexport = fields[1].rsplit(":", 1)
                port = int(hexport, 16)
                inode = int(fields[9])
            except (ValueError, IndexError):
                continue
            if port != PORT:
                continue
            # /proc/net/tcp encodes IPv4 as little-endian hex bytes.
            # IPv6 listeners cannot prove ownership of these exact IPv4
            # endpoints, so do not silently substitute a wildcard socket.
            if name != "tcp" or len(address) != 8:
                continue
            try:
                ip = socket.inet_ntoa(bytes.fromhex(address)[::-1])
            except (OSError, ValueError):
                continue
            found.setdefault(ip, set()).add(inode)
    return found


def process_socket_inodes(proc_root: Path, pids: list[int]) -> set[int]:
    sockets: set[int] = set()
    for pid in pids:
        directory = proc_root / str(pid) / "fd"
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            raise ValueError("proc_fd_inventory_inaccessible") from exc
        if len(entries) > 2048:
            raise ValueError("proc_fd_inventory_exceeds_cap")
        for fd in entries:
            try:
                link = os.readlink(fd)
            except OSError as exc:
                # A close race could hide a socket. Without a full atomic
                # inventory no ownership claim is supportable.
                raise ValueError("proc_fd_readlink_unavailable") from exc
            match = SOCKET.fullmatch(link)
            if match:
                sockets.add(int(match.group(1)))
    return sockets


def mapped_model_in_process(proc_root: Path, pids: list[int],
                            model_path: Path) -> bool:
    # A GGUF mapping is an independent process-side signal, but still
    # insufficient without a matching live API inventory and source signature.
    for pid in pids:
        try:
            with (proc_root / str(pid) / "maps").open("rb") as stream:
                content = stream.read(8 * 1024 * 1024 + 1)
        except OSError as exc:
            raise ValueError("proc_memory_map_unavailable") from exc
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("proc_memory_map_exceeds_cap")
        for line in content.decode("utf-8", "replace").splitlines():
            parts = line.split(maxsplit=5)
            if len(parts) == 6 and parts[5] == str(model_path):
                return True
    return False


def catalog(url: str) -> dict:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url + "/models", method="GET",
                             headers={"Accept": "application/json"}), timeout=4) as res:
        if int(res.status) != 200:
            raise ValueError("api_non_200")
        payload = res.read(MAX_API_BYTES + 1)
    if len(payload) > MAX_API_BYTES:
        raise ValueError("api_oversized")
    body = json.loads(payload)
    if not isinstance(body, dict) or body.get("error") is not None:
        raise ValueError("api_error_body")
    models = body.get("data")
    if not isinstance(models, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str)
        for item in models
    ):
        raise ValueError("invalid_catalog")
    return {"reachable": True, "loaded_count": len(models),
            "expected_model_present": any(item["id"] == MODEL_ID for item in models)}


def boot_digest(proc_root: Path) -> str:
    raw = (proc_root / "sys/kernel/random/boot_id").read_bytes()
    if not raw or len(raw) > 128:
        raise ValueError("invalid_boot_id")
    return hashlib.sha256(raw).hexdigest()


def observe(*, proc_root: Path = PROC, read_model_sha: bool = True) -> dict:
    report = {
        "kind": "x1-k2-local-physical-observation-v1",
        "collected_at_utc": datetime.now(UTC).isoformat(),
        "mode": "read_only_non_admitting",
        "node": "x1-370",
        "physical_process_socket_chain_verified": False,
        "model_loaded_physically_verified": False,
        "operator_approval_verified": False,
        "cryptographic_witness_signature_present": False,
        "can_dispatch": False,
        "gate": "HOLD",
        "reasons": [],
        "process": {},
        "paths": {},
        "model_id_expected": MODEL_ID,
        "model_artifact_sha256_observed": None,
        "model_artifact_bytes_observed": None,
    }
    try:
        report["app_version"] = str(json.loads(PACKAGE.read_text()).get("version") or "")
    except (OSError, ValueError, TypeError):
        report["app_version"] = None
    report["reviewed_app_version_match"] = report["app_version"] == REVIEWED_VERSION
    if not report["reviewed_app_version_match"]:
        report["reasons"].append("app_version_not_reviewed")
    if read_model_sha:
        try:
            if MODEL.is_file() and not MODEL.is_symlink():
                measured_size = MODEL.stat().st_size
                report["model_artifact_bytes_observed"] = measured_size
                if measured_size == EXPECTED_MODEL_BYTES:
                    observed_hash = digest_file(MODEL)
                    report["model_artifact_sha256_observed"] = observed_hash
                    report["model_artifact_sha256_match"] = (
                        observed_hash == EXPECTED_MODEL_SHA
                    )
                else:
                    report["model_artifact_sha256_match"] = False
            else:
                report["model_artifact_sha256_match"] = False
        except (OSError, ValueError):
            report["model_artifact_sha256_match"] = False
    else:
        report["model_artifact_sha256_match"] = None
    if report["model_artifact_sha256_match"] is not True:
        report["reasons"].append("model_artifact_sha256_unverified")
    # Public, bounded step labels identify the first failed collection
    # boundary without ever exposing /proc paths, command lines or file IDs.
    collection_stage = "runtime_process_inventory"
    try:
        inventory = process_tree(proc_root)
        pid = inventory["root_pid"]
        report["process"]["pid"] = pid
        report["process"]["start_ticks"] = inventory["start_ticks"]
        report["process"]["descendants_count"] = len(inventory["descendants"])
        report["process"]["proxy_processes_observed_unverified"] = inventory["proxies"]
        collection_stage = "boot_identity"
        report["process"]["boot_id_sha256"] = boot_digest(proc_root)
        collection_stage = "runtime_executable_link"
        proc_exe = proc_root / str(pid) / "exe"
        # The /proc/PID/exe descriptor refers to the actual running
        # executable, not to a mutable pathname on disk.
        exe_target = os.readlink(proc_exe)
        report["process"]["executable_target_verified"] = (
            exe_target == str(EXECUTABLE)
        )
        if not report["process"]["executable_target_verified"]:
            raise ValueError("running_executable_path_mismatch")
        collection_stage = "runtime_executable_hash"
        report["process"]["running_executable_sha256"] = digest_file(proc_exe)
        collection_stage = "installed_executable_hash"
        report["process"]["installed_executable_sha256_match"] = (
            digest_file(EXECUTABLE) == report["process"]["running_executable_sha256"])
        if not report["process"]["installed_executable_sha256_match"]:
            raise ValueError("running_executable_image_differs_from_installed")
        collection_stage = "listener_inventory"
        all_listeners = listener_inodes(proc_root)
        pids = inventory["descendants"]
        collection_stage = "runtime_listener_ownership"
        lmstudio_owned = process_socket_inodes(proc_root, pids)
        ownership = {
            "loopback": bool(lmstudio_owned & all_listeners.get("127.0.0.1", set())),
        }
        report["process"]["proxies"] = {}
        for kind, bind_ip in PROXY_IPS.items():
            proxy = inventory["proxies"][kind]
            proxy_pid = proxy["pid"]
            # The forwarding command is checked against an exact literal
            # in process_tree; require the actual running socat executable
            # and socket FD, not a configured systemd service alone.
            collection_stage = f"proxy_{kind}_executable_link"
            proxy_exe = proc_root / str(proxy_pid) / "exe"
            if os.readlink(proxy_exe) != str(PROXY_EXECUTABLE):
                raise ValueError("transport_proxy_running_executable_mismatch")
            collection_stage = f"proxy_{kind}_executable_hash"
            proxy_exe_sha = digest_file(proxy_exe)
            if proxy_exe_sha != digest_file(PROXY_EXECUTABLE):
                raise ValueError("transport_proxy_executable_fingerprint_mismatch")
            collection_stage = f"proxy_{kind}_listener_ownership"
            proxy_owned = process_socket_inodes(proc_root, [proxy_pid])
            ownership[kind] = bool(proxy_owned & all_listeners.get(bind_ip, set()))
            report["process"]["proxies"][kind] = {
                "pid": proxy_pid,
                "start_ticks": proxy["start_ticks"],
                "running_executable_sha256": proxy_exe_sha,
                "exact_forward_to_loopback_configured": True,
                "listener_inode_owned": ownership[kind],
            }
        report["process"]["per_transport_socket_owner_verified"] = ownership
        report["physical_process_socket_chain_verified"] = all(ownership.values())
        if not report["physical_process_socket_chain_verified"]:
            report["reasons"].append("socket_inode_owner_unverified")
        collection_stage = "mapped_model_inventory"
        report["process"]["model_file_mapped_in_process_tree"] = (
            mapped_model_in_process(proc_root, pids, MODEL)
        )
        if not report["process"]["model_file_mapped_in_process_tree"]:
            report["reasons"].append("model_file_not_mapped_by_runtime")
    except (OSError, ValueError) as exc:
        # Only public static error kinds are emitted. Don't expose
        # private /proc values or a raw exception path.
        report["reasons"].append("process_identity_or_socket_unverified")
        report["process"]["collection_error_kind"] = type(exc).__name__
        report["process"]["collection_error_stage"] = collection_stage
    for kind, url in PATHS.items():
        try:
            report["paths"][kind] = catalog(url)
        except Exception as exc:
            report["paths"][kind] = {
                "reachable": False, "expected_model_present": False,
                "collection_error_kind": type(exc).__name__,
            }
    visible_everywhere = all(
        item["expected_model_present"] for item in report["paths"].values()
    )
    if not visible_everywhere:
        report["reasons"].append("expected_model_missing_on_one_or_more_paths")
    # A process can disappear or a PID can be reused during collection.
    # Re-read the original start ticks after all API observations; never
    # rely solely on matching a process's numerical PID.
    if report["physical_process_socket_chain_verified"]:
        try:
            original = report["process"]
            if (proc_stat(proc_root / str(original["pid"]) / "stat")["start_ticks"]
                    != original["start_ticks"]):
                raise ValueError("pid_start_ticks_changed_during_collection")
            for proxy in original["proxies"].values():
                if (proc_stat(proc_root / str(proxy["pid"]) / "stat")["start_ticks"]
                        != proxy["start_ticks"]):
                    raise ValueError("proxy_pid_start_ticks_changed_during_collection")
        except (OSError, ValueError):
            report["physical_process_socket_chain_verified"] = False
            report["reasons"].append("runtime_pid_reused_or_disappeared")
    report["model_loaded_physically_verified"] = all((
        report["physical_process_socket_chain_verified"],
        report["process"].get("model_file_mapped_in_process_tree") is True,
        report["model_artifact_sha256_match"] is True,
        visible_everywhere,
    ))
    # Even complete local observations would be only self-reported evidence:
    # they require independent operator custody, signed origin and graph
    # fencing to become admissible.
    report["reasons"].append("independent_signed_witness_and_approval_missing")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-model-sha", action="store_true",
                        help="diagnostic quick mode; marks artifact hash unverified")
    args = parser.parse_args()
    result = observe(read_model_sha=not args.skip_model_sha)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
