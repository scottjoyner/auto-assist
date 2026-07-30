from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

MAX_PATCH_BYTES = 524_288


def prepare_repository(
    contract: dict[str, Any],
    *,
    task_id: str,
    execution_attempt: int = 0,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    environment = env if env is not None else os.environ
    base_root, reason = _resolve_repository(contract, environment)
    if not base_root:
        return {
            "ok": False,
            "reason": reason,
            "repository": contract.get("repository"),
        }
    head = _run(["git", "rev-parse", "HEAD"], base_root)
    if head["returncode"] != 0:
        return {"ok": False, "reason": "git_head_failed"}
    workspace_id = hashlib.sha256(
        (
            f"{contract.get('repository')}:{task_id}:{execution_attempt}:"
            f"{head['stdout'].strip()}"
        ).encode()
    ).hexdigest()[:24]
    workspace_root = Path(
        environment.get(
            "ASSISTX_IMPROVEMENT_WORKTREE_ROOT",
            str(Path(tempfile.gettempdir()) / "assistx-improvement-worktrees"),
        )
    ).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    root = workspace_root / workspace_id
    if root.exists():
        return {
            "ok": False,
            "reason": "isolated_worktree_already_exists",
            "workspace_id": workspace_id,
        }
    created = _run(
        ["git", "worktree", "add", "--detach", str(root), head["stdout"].strip()],
        base_root,
    )
    if created["returncode"] != 0:
        return {
            "ok": False,
            "reason": "isolated_worktree_create_failed",
            "detail": created["stderr"][-500:],
        }
    status = _run(["git", "status", "--porcelain", "--untracked-files=all"], root)
    if status["returncode"] != 0 or status["stdout"].strip():
        cleanup_worktree({"base_root": str(base_root), "root": str(root)})
        return {"ok": False, "reason": "isolated_worktree_not_clean"}
    return {
        "ok": True,
        "root": str(root),
        "base_root": str(base_root),
        "head": head["stdout"].strip(),
        "clean_before": True,
        "isolated": True,
        "workspace_id": workspace_id,
    }


def collect_executor_evidence(
    contract: dict[str, Any],
    prepared: dict[str, Any],
    reported: dict[str, Any] | None,
    *,
    executor_id: str,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    environment = env if env is not None else os.environ
    if not prepared.get("ok"):
        return {
            "evidence_source": "executor",
            "executor_id": executor_id,
            "worktree_clean_before": False,
            "isolated_worktree": False,
            "changed_files": [],
            "diff_lines": 0,
            "tools_used": [],
            "verification": [],
            "summary": prepared.get("reason"),
            "executor_error": prepared.get("reason"),
        }
    root = Path(prepared["root"])
    status = _run(["git", "status", "--porcelain", "--untracked-files=all"], root)
    changed_files = _status_paths(status["stdout"])
    allowed = set(contract.get("allowed_paths") or [])
    scope_ok = bool(changed_files) and all(path in allowed for path in changed_files)
    diff_lines = _diff_line_count(root, changed_files)
    verification = []
    if status["returncode"] == 0 and scope_ok:
        timeout = max(
            10,
            min(
                int(
                    environment.get(
                        "ASSISTX_IMPROVEMENT_VERIFY_TIMEOUT_SECONDS", "120"
                    )
                ),
                600,
            ),
        )
        for command in contract.get("verification_commands") or []:
            result = _run(command, root, timeout=timeout)
            verification.append(
                {
                    "command": command,
                    "returncode": result["returncode"],
                    "stdout": result["stdout"][-4000:],
                    "stderr": result["stderr"][-4000:],
                }
            )
    patch = _build_patch(root, changed_files) if scope_ok else ""
    patch_bytes = patch.encode()
    max_patch_bytes = min(
        int(contract.get("max_patch_bytes") or MAX_PATCH_BYTES),
        MAX_PATCH_BYTES,
    )
    patch_ok = bool(patch) and len(patch_bytes) <= max_patch_bytes
    evidence = {
        "evidence_source": "executor",
        "executor_id": executor_id,
        "worktree_clean_before": bool(prepared.get("clean_before")),
        "isolated_worktree": bool(prepared.get("isolated")),
        "workspace_id": prepared.get("workspace_id"),
        "head_before": prepared.get("head"),
        "changed_files": changed_files,
        "diff_lines": diff_lines,
        "tools_used": list((reported or {}).get("tools_used") or []),
        "verification": verification,
        "summary": str((reported or {}).get("summary") or ""),
        "next_candidate": (reported or {}).get("next_candidate"),
        "scope_validated": scope_ok,
        "patch": patch if patch_ok else "",
        "patch_bytes": len(patch_bytes),
        "patch_sha256": hashlib.sha256(patch_bytes).hexdigest() if patch_ok else "",
        "patch_error": None if patch_ok else "patch_missing_or_too_large",
    }
    key_id = environment.get("ASSISTX_IMPROVEMENT_ATTESTATION_KEY_ID", "").strip()
    secret = environment.get(
        "ASSISTX_IMPROVEMENT_ATTESTATION_SECRET", ""
    ).strip()
    if key_id and secret:
        evidence = sign_executor_evidence(evidence, key_id=key_id, secret=secret)
    return evidence


def cleanup_worktree(prepared: dict[str, Any]) -> dict[str, Any]:
    root_value = prepared.get("root")
    base_value = prepared.get("base_root")
    if not root_value or not base_value:
        return {"cleaned": False, "reason": "worktree_metadata_missing"}
    root = Path(str(root_value)).resolve()
    base_root = Path(str(base_value)).resolve()
    removed = _run(["git", "worktree", "remove", "--force", str(root)], base_root)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    _run(["git", "worktree", "prune"], base_root)
    return {
        "cleaned": not root.exists(),
        "returncode": removed["returncode"],
        "detail": removed["stderr"][-500:],
    }


def sign_executor_evidence(
    evidence: dict[str, Any],
    *,
    key_id: str,
    secret: str,
) -> dict[str, Any]:
    signed = dict(evidence)
    signed.pop("attestation", None)
    signature = hmac.new(
        secret.encode(),
        _canonical(signed),
        hashlib.sha256,
    ).hexdigest()
    signed["attestation"] = {
        "algorithm": "hmac-sha256",
        "key_id": key_id,
        "signature": signature,
    }
    return signed


def verify_executor_evidence(
    evidence: dict[str, Any],
    *,
    verify_keys: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[bool, str]:
    attestation = evidence.get("attestation")
    if not isinstance(attestation, dict):
        return False, "missing_executor_attestation"
    if attestation.get("algorithm") != "hmac-sha256":
        return False, "unsupported_executor_attestation"
    keys = verify_keys
    if keys is None:
        environment = env if env is not None else os.environ
        try:
            value = json.loads(
                environment.get("ASSISTX_IMPROVEMENT_VERIFY_KEYS", "{}")
            )
            keys = value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            keys = {}
    key_id = str(attestation.get("key_id") or "")
    secret = str((keys or {}).get(key_id) or "")
    if not secret:
        return False, "executor_attestation_key_unknown"
    unsigned = dict(evidence)
    unsigned.pop("attestation", None)
    expected = hmac.new(secret.encode(), _canonical(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(
        expected, str(attestation.get("signature") or "")
    ):
        return False, "executor_attestation_invalid"
    return True, ""


def promote_patch(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    *,
    expected_fingerprint: str,
    verify_keys: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    environment = env if env is not None else os.environ
    verified, reason = verify_executor_evidence(
        evidence, verify_keys=verify_keys, env=environment
    )
    if not verified:
        return {"promoted": False, "reason": reason}
    patch = str(evidence.get("patch") or "")
    fingerprint = hashlib.sha256(patch.encode()).hexdigest() if patch else ""
    if not patch or not hmac.compare_digest(fingerprint, expected_fingerprint):
        return {"promoted": False, "reason": "patch_fingerprint_mismatch"}
    if fingerprint != evidence.get("patch_sha256"):
        return {"promoted": False, "reason": "signed_patch_digest_mismatch"}
    base_root, resolve_reason = _resolve_repository(contract, environment)
    if not base_root:
        return {"promoted": False, "reason": resolve_reason}
    head = _run(["git", "rev-parse", "HEAD"], base_root)
    if head["stdout"].strip() != evidence.get("head_before"):
        return {"promoted": False, "reason": "repository_head_drifted"}
    status = _run(["git", "status", "--porcelain", "--untracked-files=all"], base_root)
    if status["returncode"] != 0 or status["stdout"].strip():
        return {"promoted": False, "reason": "promotion_target_not_clean"}
    numstat = _run(["git", "apply", "--numstat", "-"], base_root, input_text=patch)
    patch_paths = _numstat_paths(numstat["stdout"])
    allowed = set(contract.get("allowed_paths") or [])
    if (
        numstat["returncode"] != 0
        or not patch_paths
        or any(path not in allowed for path in patch_paths)
    ):
        return {"promoted": False, "reason": "promotion_patch_outside_contract"}
    checked = _run(
        ["git", "apply", "--check", "--whitespace=error-all", "-"],
        base_root,
        input_text=patch,
    )
    if checked["returncode"] != 0:
        return {
            "promoted": False,
            "reason": "promotion_patch_check_failed",
            "detail": checked["stderr"][-500:],
        }
    applied = _run(
        ["git", "apply", "--whitespace=error-all", "-"],
        base_root,
        input_text=patch,
    )
    if applied["returncode"] != 0:
        return {"promoted": False, "reason": "promotion_patch_apply_failed"}
    verification = []
    for command in contract.get("verification_commands") or []:
        result = _run(command, base_root, timeout=600)
        verification.append(
            {
                "command": command,
                "returncode": result["returncode"],
                "stdout": result["stdout"][-4000:],
                "stderr": result["stderr"][-4000:],
            }
        )
    if any(item["returncode"] != 0 for item in verification):
        rolled_back = _run(["git", "apply", "-R", "-"], base_root, input_text=patch)
        return {
            "promoted": False,
            "reason": "promotion_verification_failed",
            "verification": verification,
            "rolled_back": rolled_back["returncode"] == 0,
        }
    return {
        "promoted": True,
        "repository": contract.get("repository"),
        "head_before": evidence.get("head_before"),
        "patch_sha256": fingerprint,
        "changed_files": sorted(patch_paths),
        "verification": verification,
    }


def _resolve_repository(
    contract: dict[str, Any],
    environment: dict[str, str],
) -> tuple[Path | None, str]:
    try:
        roots = json.loads(environment.get("ASSISTX_REPOSITORY_ROOTS_JSON", "{}"))
    except json.JSONDecodeError:
        roots = {}
    repository = str(contract.get("repository") or "")
    configured = roots.get(repository) if isinstance(roots, dict) else None
    if not configured:
        return None, "repository_root_not_configured"
    root = Path(str(configured)).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        return None, "configured_repository_is_not_a_git_worktree"
    return root, ""


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def _run(
    command: list[str],
    cwd: Path,
    *,
    timeout: int = 30,
    input_text: str | None = None,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": 124, "stdout": "", "stderr": str(exc)}


def _status_paths(output: str) -> list[str]:
    paths = []
    for line in output.splitlines():
        value = line[3:].strip()
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if value:
            paths.append(value.replace("\\", "/"))
    return sorted(set(paths))


def _numstat_paths(output: str) -> list[str]:
    paths = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            paths.append(parts[2].replace("\\", "/"))
    return sorted(set(paths))


def _diff_line_count(root: Path, changed_files: list[str]) -> int:
    tracked = _run(["git", "diff", "--numstat", "HEAD", "--"], root)
    total = 0
    counted = set()
    for line in tracked["stdout"].splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        counted.add(path)
        if added.isdigit():
            total += int(added)
        if deleted.isdigit():
            total += int(deleted)
    for relative in set(changed_files) - counted:
        path = root / relative
        if path.is_file() and not path.is_symlink():
            try:
                total += len(path.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                total += 1
    return total


def _build_patch(root: Path, changed_files: list[str]) -> str:
    if not changed_files:
        return ""
    untracked = []
    tracked = _run(["git", "ls-files", "--others", "--exclude-standard"], root)
    if tracked["returncode"] == 0:
        untracked = [
            path for path in tracked["stdout"].splitlines() if path in changed_files
        ]
    if untracked:
        intent = _run(["git", "add", "--intent-to-add", "--", *untracked], root)
        if intent["returncode"] != 0:
            return ""
    patch = _run(
        ["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--", *changed_files],
        root,
    )
    return patch["stdout"] if patch["returncode"] == 0 else ""
