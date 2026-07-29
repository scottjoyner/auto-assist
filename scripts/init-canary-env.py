#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import secrets
import stat
import sys
from pathlib import Path

from assistx.deployment_canary import load_environment_file

INHERITED_KEYS = {
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "LLM_MODEL",
    "EMBED_MODEL",
}


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = []
    seen = set()
    for line in template.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in values:
                line = f"{key}={values[key]}"
                seen.add(key)
        rendered.append(line)
    for key in sorted(values.keys() - seen):
        rendered.append(f"{key}={values[key]}")
    return "\n".join(rendered) + "\n"


def initialize(
    *,
    template_path: Path,
    source_path: Path | None,
    output_path: Path,
    node_output_path: Path,
    canary_node_id: str,
    recovery_node_id: str,
    force: bool,
) -> None:
    for path in (output_path, node_output_path):
        if path.exists() and not force:
            raise FileExistsError(f"{path} already exists; use --force")
    source = (
        load_environment_file(source_path)
        if source_path and source_path.exists()
        else {}
    )
    canary_node_token = secrets.token_hex(32)
    recovery_node_token = secrets.token_hex(32)
    runbook_secret = secrets.token_hex(32)
    improvement_secret = secrets.token_hex(32)
    runbook_key_id = "runbook-canary-v1"
    improvement_key_id = f"{canary_node_id}-improvement-v1"
    values = {
        key: source[key]
        for key in INHERITED_KEYS
        if source.get(key)
    }
    values.update(
        {
            "BASIC_AUTH_USER": "canary-operator",
            "BASIC_AUTH_PASS": secrets.token_hex(24),
            "CANARY_NODE_ID": canary_node_id,
            "CANARY_NODE_TOKEN": canary_node_token,
            "CANARY_RECOVERY_NODE_ID": recovery_node_id,
            "ASSISTX_KV_PREFIX_HMAC_SECRET": secrets.token_hex(32),
            "ASSISTX_FLEET_NODE_TOKENS": json.dumps(
                {
                    canary_node_id: canary_node_token,
                    recovery_node_id: recovery_node_token,
                },
                separators=(",", ":"),
            ),
            "FLEET_NODE_TOKEN": canary_node_token,
            "ASSISTX_RUNBOOK_SIGNING_KEYS": json.dumps(
                {runbook_key_id: runbook_secret},
                separators=(",", ":"),
            ),
            "ASSISTX_RUNBOOK_ACTIVE_KEY_ID": runbook_key_id,
            "FLEET_RUNBOOK_VERIFY_KEYS": json.dumps(
                {runbook_key_id: runbook_secret},
                separators=(",", ":"),
            ),
            "ASSISTX_IMPROVEMENT_ATTESTATION_KEY_ID": improvement_key_id,
            "ASSISTX_IMPROVEMENT_ATTESTATION_SECRET": improvement_secret,
            "ASSISTX_IMPROVEMENT_VERIFY_KEYS": json.dumps(
                {improvement_key_id: improvement_secret},
                separators=(",", ":"),
            ),
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_template(template_path.read_text(), values))
    output_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    node_values = {
        "FLEET_NODE_ID": recovery_node_id,
        "FLEET_NODE_TOKEN": recovery_node_token,
        "FLEET_RECOVERY_RUNBOOKS_ENABLED": "false",
        "FLEET_RUNBOOK_VERIFY_KEYS": json.dumps(
            {runbook_key_id: runbook_secret},
            separators=(",", ":"),
        ),
        "FLEET_UNSAFE_SHELL_TASKS_ENABLED": "false",
    }
    node_output_path.parent.mkdir(parents=True, exist_ok=True)
    node_output_path.write_text(
        "# Install these values only on the selected recovery canary node.\n"
        + "\n".join(f"{key}={value}" for key, value in node_values.items())
        + "\n"
    )
    node_output_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Initialize untracked AssistX canary environment files."
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("deploy/canary.env.example"),
    )
    parser.add_argument("--source", type=Path, default=Path(".env"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("deploy/canary.env"),
    )
    parser.add_argument(
        "--node-output",
        type=Path,
        default=Path("deploy/canary-recovery-node.env"),
    )
    parser.add_argument("--canary-node-id", default="assistx-canary")
    parser.add_argument("--recovery-node-id", default="xwing")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        initialize(
            template_path=args.template,
            source_path=args.source,
            output_path=args.output,
            node_output_path=args.node_output,
            canary_node_id=args.canary_node_id,
            recovery_node_id=args.recovery_node_id,
            force=args.force,
        )
    except (OSError, ValueError) as exc:
        print(f"[init-canary-env] ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"[init-canary-env] wrote {args.output} (mode 0600)")
    print(f"[init-canary-env] wrote {args.node_output} (mode 0600)")
    print("[init-canary-env] secrets were generated but not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
