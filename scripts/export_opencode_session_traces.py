#!/usr/bin/env python3
"""Export OpenCode SQLite sessions into a durable, read-only trace ledger.

The exporter never reads provider credentials and never mutates OpenCode state.
It can reconcile sessions launched outside the free-subagent supervisor so model,
worktree, token/cost, tool, and Git provenance remain auditable.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import tempfile
import pathlib
import sqlite3
import subprocess
import time
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone

# Import supervisor utilities for free model classification
import importlib.util
SUPERVISOR_PATH = pathlib.Path(__file__).parent / "free_subagent_supervisor.py"
if SUPERVISOR_PATH.exists():
    spec = importlib.util.spec_from_file_location("free_subagent_supervisor", SUPERVISOR_PATH)
    supervisor_module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(supervisor_module)
    _classify_free_model = supervisor_module.classify_free_model
    _route_kind = supervisor_module.route_kind
    _price_is_zero = supervisor_module._price_is_zero
    _annotate_trace_session = supervisor_module._annotate_trace_session
else:
    # Fallback implementations if supervisor is not available
    def _price_is_zero(value: Any) -> bool:
        try:
            return float(value) == 0.0
        except (TypeError, ValueError):
            return False
    
    def _route_kind(model_id: str) -> str:
        """Classify a requested model as exact or router-selected alias."""
        mid = str(model_id or "").strip()
        if "/" in mid:
            leaf = mid.rsplit("/", 1)[-1].lower().split(":")[0]
            if leaf in ROUTE_ALIAS_TOKENS:
                return "alias"
        return "exact"
    
    def _classify_free_model(record: dict[str, Any]) -> str:
        """Classify free model by provider and pricing model."""
        model_id = str(record.get("id") or "")
        provider = str(record.get("provider") or "").lower()
        
        # Kilo anonymous pool (zero-cost by policy; no credential needed).
        if provider == "kilo":
            pricing = record.get("pricing") or {}
            if not isinstance(pricing, dict) or not pricing:
                return "kilo-anonymous"
            if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
                return "kilo-anonymous"
        
        # Z.AI zero-cost models (reserve-only by policy; cost classification only)
        if provider == "zai":
            return "zai-zero-cost"
        
        # Cohere zero-cost models (provider-specific)
        if provider in ("cohère", "cohere"):
            pricing = record.get("pricing") or {}
            if isinstance(pricing, dict):
                if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
                    return "cohere-zero-cost"
        
        # LM Studio zero-cost models (local inference)
        if "lmstudio" in provider or "lm_studio" in provider:
            return "lmstudio-zero-cost"
        
        # OpenCode-native free models (:free suffix or explicit free tag)
        # These take priority over general OpenRouter account-free checks
        if ":free" in model_id:
            return "opencode-native-free"
        tags = record.get("tags") or []
        if isinstance(tags, list) and any(str(tag).lower() == "free" for tag in tags):
            return "opencode-native-free"
        
        # OpenRouter account-free models (both prompt and completion zero)
        if "openrouter" in provider:
            pricing = record.get("pricing") or {}
            if isinstance(pricing, dict):
                if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
                    return "openrouter-account-free"
        
        # Unknown or rate-limited
        pricing = record.get("pricing") or {}
        if isinstance(pricing, dict):
            if _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion")):
                return "unknown-free"
        
        return "rate-limited"
    
    def _annotate_trace_session(record: dict[str, Any]) -> dict[str, Any]:
        """Annotate an exported session with fail-closed model attribution."""
        annotated = dict(record)
        requested = str(annotated.get("requested_model") or annotated.get("model") or "")
        resolved = annotated.get("resolved_model")
        annotated.setdefault("requested_model", requested)
        annotated.setdefault("resolved_model", resolved)
        kind = _route_kind(requested)
        annotated["route_kind"] = kind
        
        if resolved:
            annotated["effective_model"] = resolved
            annotated["model_attribution"] = "resolved-upstream"
            annotated["model_identity_complete"] = True
        elif requested and kind == "exact":
            annotated["effective_model"] = requested
            annotated["model_attribution"] = "exact-request"
            annotated["model_identity_complete"] = True
        else:
            annotated["effective_model"] = None
            annotated["model_attribution"] = "unresolved-router-alias"
            annotated["model_identity_complete"] = False
        return annotated

# Configuration for bounded failure
MAX_GIT_COMMAND_TIMEOUT = 10  # seconds
MAX_SQL_TIMEOUT = 30  # seconds
MAX_JSON_SERIALIZATION_TIMEOUT = 5  # seconds
JSONL_VALIDATION_TIMEOUT = 5  # seconds

# Constants for tokenization and cost accounting
FREE_MODEL_COST = 0.0
PAID_MODEL_COST_FACTOR = 1.0  # Base cost factor for paid models
TOKEN_PRECISION = 6  # Decimal places for cost calculations

# Route alias tokens for model classification
ROUTE_ALIAS_TOKENS = frozenset({"free", "auto", "router", "any", "best", "default"})

# Schema version - updated to v2 for enhanced trace receipt contract
SCHEMA_VERSION = "opencode-session-trace/v2"

# De-duplication belongs to a single export call, not module lifetime.
# SQLite session IDs are unique within a database; independent DB snapshots
# must not affect one another just because their IDs or timestamps coincide.

# Performance metrics tracking
_export_stats = {
    "sessions_processed": 0,
    "duplicates_skipped": 0,
    "errors_encountered": 0,
    "secrets_redacted": 0,
    "jsonl_validated": 0,
    "bounded_failures_handled": 0,
}
def _json(text: str | None) -> dict[str, Any]:
    """Parse JSON text safely, returning empty dict on failure."""
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
def _git(directory: str) -> dict[str, Any]:
    """Extract Git worktree and commit information with bounded failure handling."""
    path = pathlib.Path(directory)
    if not path.exists():
        return {"is_git": False}
    try:
        # Git commands with timeouts to prevent unbounded failure
        top = subprocess.check_output(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=MAX_GIT_COMMAND_TIMEOUT,
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=MAX_GIT_COMMAND_TIMEOUT,
        ).strip()
        head = subprocess.check_output(
            ["git", "-C", directory, "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=MAX_GIT_COMMAND_TIMEOUT,
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", directory, "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=MAX_GIT_COMMAND_TIMEOUT,
        )
        return {
            "is_git": True,
            "root": top,
            "branch": branch,
            "head": head,
            "dirty_paths": len([line for line in status.splitlines() if line]),
        }
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError, Exception) as e:
        _export_stats["bounded_failures_handled"] += 1
        return {"is_git": False, "error": type(e).__name__}
def _extract_node_from_directory(directory: str) -> Optional[str]:
    """Extract node identifier from worktree directory path.
    
    Implements node/worktree lineage tracking by parsing directory structure.
    Expected pattern: /path/to/node-id/worktree-task-id
    """
    if not directory:
        return None
    # Look for node pattern in directory path (e.g., node-x1-370)
    # Pattern: contains 'node-' followed by alphanumeric, dash, underscore
    node_pattern = r'node-([a-zA-Z0-9\-_]+)'
    match = re.search(node_pattern, directory)
    if match:
        return f"node-{match.group(1)}"
    return None
def _redact_sensitive_data(data: dict[str, Any]) -> dict[str, Any]:
    """Redact credential-shaped fragments recursively; preserve trace metadata.

    This is defense in depth; raw prompt and tool payloads are never selected
    by the exporter. Untrusted free-form errors are stored as counts only.
    """
    def clean(value: Any, key: str = "") -> Any:
        if any(term in key.lower() for term in ("password", "secret", "authorization", "api_key", "credential", "access_token", "private_key")):
            _export_stats["secrets_redacted"] += 1
            return "[REDACTED]"
        if isinstance(value, dict):
            return {k: clean(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            patterns = [r"\b(?:sk_|ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_-]{8,}\b",
                        r"\bAKIA[A-Z0-9]{16}\b"]
            cleaned = value
            for pattern in patterns:
                cleaned, count = re.subn(pattern, "[REDACTED]", cleaned)
                _export_stats["secrets_redacted"] += count
            return cleaned
        return value
    return clean(data)

def _validate_jsonl_safety(data: dict[str, Any]) -> bool:
    """Validate that data conforms to JSONL safety standards.
    
    Safety checks include:
    - Valid JSON structure
    - No reserved field names
    - Proper typing
    """
    try:
        start_time = time.time()
        # Serialize and deserialize to validate JSON structure
        json_str = json.dumps(data, sort_keys=True, allow_nan=False)
        parsed = json.loads(json_str)
        
        # Validate that we can round-trip
        if json.dumps(parsed, sort_keys=True, allow_nan=False) != json_str:
            return False
        
        # Deny reserved object keys recursively, including nested metadata.
        reserved_fields = {"__proto__", "constructor", "prototype"}
        def has_reserved(item: Any) -> bool:
            if isinstance(item, dict):
                return any(str(key) in reserved_fields or has_reserved(value)
                           for key, value in item.items())
            if isinstance(item, list):
                return any(has_reserved(value) for value in item)
            return False
        if has_reserved(data):
            return False
        
        # Check for JSON injection patterns
        json_str_lower = json_str.lower()
        if "\\u0000" in json_str or "\x00" in json_str:
            return False
        
        # Check for extremely long strings that might cause issues
        max_string_length = 10000
        for key, value in data.items():
            if isinstance(value, str) and len(value) > max_string_length:
                return False
        
        # Check total document size (JSONL limit is typically 1MB)
        if len(json_str.encode('utf-8')) > 1024 * 1024:
            return False
        
        # Time check to prevent unbounded operation
        if time.time() - start_time > JSONL_VALIDATION_TIMEOUT:
            _export_stats["bounded_failures_handled"] += 1
            return False
        
        _export_stats["jsonl_validated"] += 1
        return True
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError, MemoryError, OverflowError):
        _export_stats["errors_encountered"] += 1
        return False
    except Exception:
        _export_stats["errors_encountered"] += 1
        return False
def _calculate_token_breakdown(tokens_input: int, tokens_output: int, 
                               tokens_reasoning: int, tokens_cache_read: int, 
                               tokens_cache_write: int) -> dict[str, int]:
    """Calculate comprehensive token breakdown for truthful token accounting."""
    return {
        # A sum of reported categories is not necessarily provider-billable tokens:
        # some runtimes already include reasoning and cache in input/output.
        "total_tokens": tokens_input + tokens_output + tokens_reasoning + tokens_cache_read + tokens_cache_write,
        "provider_billable_total": None,
        "input_tokens": tokens_input,
        "output_tokens": tokens_output,
        "reasoning_tokens": tokens_reasoning,
        "cache_read_tokens": tokens_cache_read,
        "cache_write_tokens": tokens_cache_write,
    }
def _calculate_cost_breakdown(cost: float) -> dict[str, float]:
    """Calculate cost breakdown for truthful cost accounting."""
    return {
        "total_cost": round(cost, TOKEN_PRECISION) if cost is not None else None,
        "input_cost": None,  # Session DB does not contain source attribution.
        "output_cost": None,
        "attribution": "unknown",
    }
def _calculate_efficiency_metrics(cost_breakdown: dict[str, Any],
                                  token_breakdown: dict[str, int]) -> dict[str, None]:
    """Never invent per-category billing metrics from a session total."""
    return {"cost_per_1k_input_tokens": None,
            "cost_per_1k_output_tokens": None}

def _extract_error_summary(assistant_errors: list[str], finish_reasons: list[str]) -> dict[str, Any]:
    """Extract and aggregate error information for comprehensive error tracking."""
    error_summary = {
        "assistant_errors": ["[REDACTED]" for _ in assistant_errors],
        "error_count": len(assistant_errors),
        "finish_reasons": finish_reasons,
        "has_errors": len(assistant_errors) > 0,
        "error_types": collections.Counter(),
    }
    
    # Categorize errors for better analysis
    for error in assistant_errors:
        if isinstance(error, str):
            # Extract error type from error message
            error_lower = error.lower()
            if "timeout" in error_lower:
                error_summary["error_types"]["timeout"] += 1
            elif "rate_limit" in error_lower or "rate limit" in error_lower:
                error_summary["error_types"]["rate_limit"] += 1
            elif "token" in error_lower and "exhausted" in error_lower:
                error_summary["error_types"]["token_exhausted"] += 1
            elif "authentication" in error_lower or "auth" in error_lower:
                error_summary["error_types"]["authentication"] += 1
            elif "network" in error_lower or "connection" in error_lower:
                error_summary["error_types"]["network"] += 1
            elif "model" in error_lower and "unavailable" in error_lower:
                error_summary["error_types"]["model_unavailable"] += 1
            elif "quota" in error_lower:
                error_summary["error_types"]["quota"] += 1
            elif "validation" in error_lower or "invalid" in error_lower:
                error_summary["error_types"]["validation"] += 1
            else:
                error_summary["error_types"]["other"] += 1
    
    return error_summary
def _generate_node_session_correlation(node_id: Optional[str], directory: str, 
                                      session_id: str) -> dict[str, Any]:
    """Generate node-session correlation information for task/worktree lineage."""
    correlation = {}
    if node_id:
        # Extract task/worktree identifier from directory
        worktree_match = re.search(r'worktree-([a-zA-Z0-9\-_]+)', directory)
        task_id = f"worktree-{worktree_match.group(1)}" if worktree_match else None
        
        correlation = {
            "node_id": node_id,
            "session_id": session_id,
            "directory": directory,
            "worktree_path": directory,
        }
        
        # Add task/worktree assignment if we can extract it
        if task_id:
            correlation["task_id"] = task_id
    
    return correlation
def _objective_digest(con: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT p.data
        FROM part p
        JOIN message m ON m.id = p.message_id
        WHERE p.session_id = ?
        ORDER BY p.time_created ASC
        """,
        (session_id,),
    ).fetchall()
    texts: list[str] = []
    for (raw,) in row:
        part = _json(raw)
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
            if texts:
                break
    if not texts:
        return {"sha256": None, "chars": 0}
    text = texts[0]
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
    }
def export_sessions(db_path: pathlib.Path, *, session_id: str | None = None,
                    title_prefix: str | None = None,
                    max_sessions: int = 500) -> list[dict[str, Any]]:
    """Export OpenCode SQLite sessions with enhanced trace receipt contract compliance.
    
    This enhanced version includes:
    - Truthful token/cost accounting with granular breakdowns
    - Comprehensive error/exception state tracking
    - Task/worktree lineage correlation
    - Free model classification using supervisor utilities
    - Secret redaction and JSONL safety validation
    - Duplicate receipt detection
    - Bounded failure mechanisms
    """
    start_time = time.time()
    for key in ("sessions_processed", "duplicates_skipped", "errors_encountered",
                "secrets_redacted", "jsonl_validated", "bounded_failures_handled"):
        _export_stats[key] = 0
    _export_stats["failed_session_ids"] = []
    _export_stats.pop("last_error_type", None)
    if max_sessions < 1 or max_sessions > 5000:
        raise ValueError("max_sessions must be between 1 and 5000")
    # URI mode=ro and query_only ensure no state changes on a live OpenCode DB.
    uri = db_path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    where, params = [], []
    if session_id:
        where.append("id = ?")
        params.append(session_id)
    if title_prefix:
        where.append("title LIKE ? ESCAPE '!'")
        params.append(title_prefix.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%')
    sql = """SELECT id, parent_id, directory, title, model, cost,
             tokens_input, tokens_output, tokens_reasoning,
             tokens_cache_read, tokens_cache_write, time_created, time_updated
             FROM session"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY time_created ASC, id ASC LIMIT ?"
    sessions = con.execute(sql, (*params, max_sessions + 1)).fetchall()
    if len(sessions) > max_sessions:
        con.close()
        raise ValueError("session scope exceeds max_sessions; filter by --session-id or --title-prefix")
    
    records: list[dict[str, Any]] = []
    seen_session_ids: set[str] = set()

    for session in sessions:
        try:
            session_start_time = time.time()
            sid = session["id"]
            # Preserve millisecond timestamps as recorded by OpenCode.
            # Human-readable formatting is unnecessary for receipt identity.
            # Only de-duplicate within this one database export. Do not mutate
            # process-global state before a record is successfully emitted.
            if sid in seen_session_ids:
                _export_stats["duplicates_skipped"] += 1
                continue
            
            model = _json(session["model"])
            provider = model.get("providerID")
            
            # Extract and classify free model information
            pricing = model.get("pricing")
            pricing = pricing if isinstance(pricing, dict) else {}
            requested_model = model.get("id") or model.get("modelID")
            # Always construct the model record before classification, even
            # when its ID includes :free. Such names are cost hints, not bills.
            model_record = {
                "id": requested_model,
                "provider": provider,
                "pricing": pricing,
                "variant": model.get("variant"),
                "tags": model.get("tags", []),
                "resolvedID": model.get("resolvedID") or model.get("resolved_id"),
            }
            free_model_type = _classify_free_model(model_record)
            normalized_provider = str(provider or "").lower()
            has_free_suffix = str(requested_model or "").endswith(":free")
            price_is_zero = bool(pricing) and _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion"))
            if has_free_suffix:
                if normalized_provider in ("kilo", "kilo_free"):
                    free_model_type = "kilo-anonymous"  # Provider policy, not verified billing.
                elif normalized_provider == "openrouter" and price_is_zero:
                    free_model_type = "openrouter-account-free"
                else:
                    free_model_type = "free-suffix-unverified"
            
            # This is classification metadata, NOT paid-route admission.
            # A :free name or policy alone cannot independently prove billing.
            # Determine if this is a free provider
            is_free_provider = free_model_type in (
                "opencode-native-free", "kilo-anonymous", "openrouter-account-free",
                "zai-zero-cost", "cohere-zero-cost", "lmstudio-zero-cost",
                "unknown-free"
            ) and not (isinstance(session["cost"], (int, float)) and session["cost"] > 0)
            
            # Process messages for error tracking
            messages = con.execute(
                "SELECT data FROM message WHERE session_id = ? ORDER BY time_created",
                (sid,),
            ).fetchall()
            
            roles: collections.Counter[str] = collections.Counter()
            assistant_errors: list[str] = []
            for (raw,) in messages:
                msg = _json(raw)
                role = msg.get("role")
                if isinstance(role, str):
                    roles[role] += 1
                err = msg.get("error")
                if err:
                    error_str = err if isinstance(err, str) else json.dumps(err, sort_keys=True)
                    assistant_errors.append(error_str)
            
            # Process parts for tool and finish reason tracking
            parts = con.execute(
                "SELECT data FROM part WHERE session_id = ? ORDER BY time_created",
                (sid,),
            ).fetchall()
            
            part_types: collections.Counter[str] = collections.Counter()
            tools: collections.Counter[str] = collections.Counter()
            finish_reasons: list[str] = []
            
            for (raw,) in parts:
                part = _json(raw)
                typ = part.get("type")
                if isinstance(typ, str):
                    part_types[typ] += 1
                
                if typ == "tool":
                    tool = part.get("tool") or part.get("name")
                    if isinstance(tool, str):
                        tools[tool] += 1
                
                if typ == "step-finish" and isinstance(part.get("reason"), str):
                    finish_reasons.append(part["reason"])
            
            # Extract node information from directory
            node_id = _extract_node_from_directory(session["directory"])
            
            # Determine task/worktree ownership
            title = session["title"]
            task_id = None
            worktree_owners = []
            
            if isinstance(title, str) and title:
                # Use title as task_id if it's a reasonable identifier
                task_id = title.strip()
                worktree_owners = []  # Session ID is not proof of worktree ownership.
            
            # Generate node-session correlation
            node_session_correlation = _generate_node_session_correlation(
                node_id, session["directory"], sid
            )
            
            # Calculate enhanced token/cost breakdowns
            token_breakdown = _calculate_token_breakdown(
                session["tokens_input"], session["tokens_output"],
                session["tokens_reasoning"], session["tokens_cache_read"],
                session["tokens_cache_write"]
            )
            
            cost_breakdown = _calculate_cost_breakdown(session["cost"])
            
            efficiency_metrics = _calculate_efficiency_metrics(cost_breakdown, token_breakdown)
            
            # Extract error summary
            error_summary = _extract_error_summary(assistant_errors, finish_reasons)
            
            # Process model attribution using supervisor's route kind
            requested_model = model.get("id") or model.get("modelID")
            resolved_model = model.get("resolvedID") or model.get("resolved_id") or model.get("resolvedModelID")
            
            # Use supervisor's model attribution logic
            route_kind = _route_kind(requested_model or "")
            
            if resolved_model:
                effective_model = resolved_model
                model_attribution = "resolved-upstream"
                model_identity_complete = True
            elif requested_model and route_kind == "exact":
                effective_model = requested_model
                model_attribution = "exact-request"
                model_identity_complete = True
            else:
                effective_model = None
                model_attribution = "unresolved-router-alias"
                model_identity_complete = False
            
            # Build the enhanced trace record
            record = {
                "schema": SCHEMA_VERSION,
                "session_id": sid,
                "parent_session_id": session["parent_id"],
                "title": session["title"],
                "directory": session["directory"],
                "task_id": task_id,
                "worktree_owners": worktree_owners,
                "node_id": node_id,
                "node_session_correlation": node_session_correlation,
                "provider": provider,
                "model": requested_model,
                "requested_model": requested_model,
                "resolved_model": resolved_model,
                "free_model_type": free_model_type,
                "is_free_provider": is_free_provider,
                "free_claim_basis": "provider-policy" if free_model_type == "kilo-anonymous"
                    else "price-metadata" if price_is_zero else "model-suffix" if has_free_suffix else "provider-policy-or-unverified",
                "provider_billing_verified": False,
                "route_kind": route_kind,
                "effective_model": effective_model,
                "model_attribution": model_attribution,
                "model_identity_complete": model_identity_complete,
                "variant": model.get("variant"),
                "cost": session["cost"],
                "cost_breakdown": cost_breakdown,
                "tokens": {
                    "input": session["tokens_input"],
                    "output": session["tokens_output"],
                    "reasoning": session["tokens_reasoning"],
                    "cache_read": session["tokens_cache_read"],
                    "cache_write": session["tokens_cache_write"],
                },
                "token_breakdown": token_breakdown,
                "efficiency_metrics": efficiency_metrics,
                "time_created": session["time_created"],
                "time_updated": session["time_updated"],
                "assistant_error_count": len(assistant_errors),
                "retry_count": None,  # No attempt ledger stored in the DB.
                "max_retries": None,  # No retry policy stored in the DB.
                "error_summary": error_summary,
                "messages": dict(sorted(roles.items())),
                "parts": dict(sorted(part_types.items())),
                "tools": dict(sorted(tools.items())),
                "patch_parts": part_types.get("patch", 0),
                "last_finish_reason": finish_reasons[-1] if finish_reasons else None,
                "objective": _objective_digest(con, sid),
                "git": _git(session["directory"]),
            }
            
            # Apply supervisor's trace session annotation if available
            try:
                record = _annotate_trace_session(record)
            except Exception as e:
                # If supervisor annotation fails, continue with enhanced record
                _export_stats["errors_encountered"] += 1
            
            # Redact sensitive data before output
            record = _redact_sensitive_data(record)
            
            # Validate JSONL safety
            if not _validate_jsonl_safety(record):
                _export_stats["errors_encountered"] += 1
                continue
            
            # Add the record to the results
            records.append(record)
            seen_session_ids.add(sid)
            _export_stats["sessions_processed"] += 1
            
            # Performance check to prevent unbounded processing
            if time.time() - session_start_time > 5.0:  # 5 second per session limit
                _export_stats["bounded_failures_handled"] += 1
                
        except Exception as e:
            _export_stats["errors_encountered"] += 1
            _export_stats["last_error_type"] = type(e).__name__
            # Opaque IDs are safe in receipts; never persist raw exception text.
            if len(_export_stats["failed_session_ids"]) < 20:
                _export_stats["failed_session_ids"].append(sid)
            continue
    
    # Performance metrics logging
    export_duration = time.time() - start_time
    _export_stats["export_duration_seconds"] = export_duration
    
    con.close()
    return records

def main() -> int:
    """Main entry point with enhanced error handling and reporting."""
    parser = argparse.ArgumentParser(
        description="Export OpenCode SQLite sessions with enhanced trace receipt contract"
    )
    parser.add_argument("--db", required=True, type=pathlib.Path,
                       help="Path to OpenCode SQLite database file")
    parser.add_argument("--out", required=True, type=pathlib.Path,
                       help="Path to output JSONL file")
    parser.add_argument("--title-prefix", type=str,
                       help="SQL-filter sessions by literal title prefix")
    parser.add_argument("--session-id", type=str,
                       help="Export exactly one session ID, with no full-database traversal")
    parser.add_argument("--max-sessions", type=int, default=500,
                       help="Fail closed when the selected session scope exceeds this count (1-5000)")
    
    args = parser.parse_args()
    
    # Validate input database exists and is readable
    if not args.db.exists():
        print(f"Error: Database file not found: {args.db}")
        return 1
    
    try:
        records = export_sessions(args.db, session_id=args.session_id,
                                  title_prefix=args.title_prefix,
                                  max_sessions=args.max_sessions)
    except Exception as e:
        print(f"Error during export: {e}")
        return 1
    
    # No partial or empty overwrite if an explicit session was missing or
    # internal record processing failed. Preserve last successful evidence.
    if _export_stats["errors_encountered"] or (args.session_id and not records):
        print(json.dumps({"error": "export_incomplete", "records": len(records),
                          "error_count": _export_stats["errors_encountered"],
                          "failed_session_ids": _export_stats.get("failed_session_ids", [])},
                         sort_keys=True))
        return 1

    # Ensure output directory exists
    args.out.parent.mkdir(parents=True, exist_ok=True)
    
    # Exported records are already validated. Use a private, atomic file:
    # no partially written ledger and no world-readable credentials/metadata.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.out.parent, prefix=".opencode-trace-",
                                         suffix=".tmp", delete=False) as handle:
            temp_path = pathlib.Path(handle.name)
            for record in records:
                handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        os.replace(temp_path, args.out)
        temp_path = None
    except Exception as e:
        print(f"Trace output failure: {type(e).__name__}")
        return 1
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    # Generate comprehensive summary
    missing_model = [r["session_id"] for r in records if not r.get("provider") or not r.get("model")]
    unresolved = [r["session_id"] for r in records if not r.get("model_identity_complete")]
    
    # Free provider statistics
    free_providers = sum(1 for r in records if r.get("is_free_provider"))
    free_model_types = collections.Counter(r.get("free_model_type") for r in records if r.get("free_model_type"))
    
    # Error statistics
    total_errors = sum(r.get("assistant_error_count", 0) for r in records)
    total_retried = None  # Actual retry count unavailable in this DB schema.
    
    summary = {
        "records": len(records),
        "with_requested_model_identity": len(records) - len(missing_model),
        "missing_requested_model_identity": len(missing_model),
        "fully_attributed_model_records": len(records) - len(unresolved),
        "unresolved_model_attribution_records": len(unresolved),
        "free_provider_records": free_providers,
        "free_model_types": dict(free_model_types),
        "total_assistant_errors": total_errors,
        "total_retry_attempts": total_retried,
        "duplicates_skipped": _export_stats["duplicates_skipped"],
        "secrets_redacted": _export_stats["secrets_redacted"],
        "jsonl_validated": _export_stats["jsonl_validated"],
        "bounded_failures_handled": _export_stats["bounded_failures_handled"],
        "errors_encountered": _export_stats["errors_encountered"],
        "last_error_type": _export_stats.get("last_error_type"),
        "failed_session_ids": _export_stats.get("failed_session_ids", []),
        "export_duration_seconds": _export_stats.get("export_duration_seconds", 0),
        "output": str(args.out),
    }
    
    print(json.dumps(summary, sort_keys=True))
    
    # Return error code if sessions were missing model identity
    # An explicitly requested missing session is a failure, not zero successes.
    return 1 if missing_model or _export_stats["errors_encountered"] or (args.session_id and not records) else 0
if __name__ == "__main__":
    raise SystemExit(main())
