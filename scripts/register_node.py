#!/usr/bin/env python3
"""
register_node.py - Self-registration script for AssistX swarm nodes.
Run this on each agent machine to register it with the swarm.

Usage:
    python3 register_node.py --node-id falcon --display-name "Falcon (demo-1)" \
        --tailscale-ip 100.65.68.58 --os linux --arch x86_64

Or use environment variables:
    export NODE_ID=falcon
    export DISPLAY_NAME="Falcon (demo-1)"
    export TAILSCALE_IP=100.65.68.58
    python3 register_node.py
"""

import argparse
import json
import subprocess
import sys
import os


def register_node(
    assistx_url: str,
    auth_user: str,
    auth_pass: str,
    node_id: str,
    display_name: str,
    tailscale_ip: str,
    os_type: str,
    arch: str,
    hostname: str | None = None,
    lan_ip: str | None = None,
    roles: list[str] | None = None,
):
    """Register a node with the AssistX swarm."""
    if hostname is None:
        hostname = node_id
    if roles is None:
        roles = ["hermes_agent", "model_endpoint"]

    payload = {
        "node_id": node_id,
        "hostname": hostname,
        "display_name": display_name,
        "status": "online",
        "roles": roles,
        "tailscale_ip": tailscale_ip,
        "lan_ip": lan_ip,
        "os": os_type,
        "arch": arch,
    }

    cmd = [
        "curl", "-s", "-u", f"{auth_user}:{auth_pass}",
        "-X", "POST",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(payload),
        f"{assistx_url}/api/swarm/nodes/register",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        response = json.loads(result.stdout)
        node = response.get("node", {})
        print(f"Registered node: {node.get('node_id')}")
        print(f"  Display name: {node.get('display_name')}")
        print(f"  Tailscale IP: {node.get('tailscale_ip')}")
        print(f"  Roles: {node.get('roles', [])}")
        print(f"  Status: {node.get('status')}")
        return True
    except json.JSONDecodeError:
        print(f"Error: {result.stdout}")
        return False


def main():
    # Get config from environment or args
    assistx_url = os.environ.get("ASSISTX_URL", "http://172.20.0.5:8000")
    auth_user = os.environ.get("ASSISTX_USER", "admin")
    auth_pass = os.environ.get("ASSISTX_PASS", "change-me")

    parser = argparse.ArgumentParser(description="Register a node with AssistX swarm")
    parser.add_argument("--node-id", default=os.environ.get("NODE_ID"), required=True)
    parser.add_argument("--display-name", default=os.environ.get("DISPLAY_NAME"), required=True)
    parser.add_argument("--tailscale-ip", default=os.environ.get("TAILSCALE_IP"), required=True)
    parser.add_argument("--os", default=os.environ.get("OS"), required=True)
    parser.add_argument("--arch", default=os.environ.get("ARCH"), required=True)
    parser.add_argument("--hostname", default=os.environ.get("HOSTNAME"))
    parser.add_argument("--lan-ip", default=os.environ.get("LAN_IP"))
    parser.add_argument("--roles", default=os.environ.get("ROLES", "hermes_agent,model_endpoint"))
    parser.add_argument("--assistx-url", default=assistx_url)
    parser.add_argument("--auth-user", default=auth_user)
    parser.add_argument("--auth-pass", default=auth_pass)
    args = parser.parse_args()

    roles = [r.strip() for r in args.roles.split(",")]
    register_node(
        assistx_url=args.assistx_url,
        auth_user=args.auth_user,
        auth_pass=args.auth_pass,
        node_id=args.node_id,
        display_name=args.display_name,
        tailscale_ip=args.tailscale_ip,
        os_type=args.os,
        arch=args.arch,
        hostname=args.hostname,
        lan_ip=args.lan_ip,
        roles=roles,
    )


if __name__ == "__main__":
    main()
