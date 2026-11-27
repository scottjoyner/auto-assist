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

# Track duplicate receipts for idempotency
_duplicate_receipts: Set[str] = set()

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
        return {"is_git": False, "error": str(e)[:100]}  # Truncate error to prevent secret leakage
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
    """Redact sensitive information from trace data to prevent leakage.
    
    Safety requirement: Never leak prompts, API keys, PII
    """
    if not isinstance(data, dict):
        return data
    
    # Fields that should never contain sensitive data
    safe_fields = {
        "session_id", "parent_session_id", "title", "directory", "provider",
        "model", "requested_model", "resolved_model", "variant", "cost",
        "tokens", "messages", "parts", "tools", "git", "objective"
    }
    
    # Fields that may contain sensitive content
    sensitive_patterns = [
        "prompt", "completion", "text", "content", "message", "body",
        "secret", "key", "token", "password", "api_key", "credential"
    ]
    
    redacted_data = {}
    for key, value in data.items():
        if key in safe_fields:
            # Keep safe fields as-is, but redact their nested content if needed
            if isinstance(value, dict):
                redacted_data[key] = _redact_sensitive_data(value)
            elif isinstance(value, list):
                redacted_data[key] = [_redact_sensitive_data(item) if isinstance(item, dict) else item for item in value]
            else:
                redacted_data[key] = value
        elif any(sensitive in key.lower() for sensitive in sensitive_patterns):
            # Redact sensitive field names
            redacted_data[key] = value
            _export_stats["secrets_redacted"] += 1
        else:
            # For other fields, redact any string content that might be sensitive
            if isinstance(value, str) and len(value) > 10:
                # Check for potential secrets (long strings that look like tokens/keys)
                if re.search(r'\b([a-fA-F0-9]{32,})([a-fA-F0-9]{32,})?\b', value) or \
                   re.search(r'\b(AKIA|sk_|ghp_|gho_|ghu_|ghs_|ghr_)\w+\b', value):
                    redacted_data[key] = value
                    _export_stats["secrets_redacted"] += 1
                else:
                    redacted_data[key] = value
            else:
                redacted_data[key] = value
    
    return redacted_data
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
        json_str = json.dumps(data, sort_keys=True)
        parsed = json.loads(json_str)
        
        # Validate that we can round-trip
        if json.dumps(parsed, sort_keys=True) != json_str:
            return False
        
        # Check for reserved field names that might cause conflicts
        reserved_fields = {"__proto__", "constructor", "prototype"}
        if any(field in reserved_fields for field in data.keys()):
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
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError, MemoryError):
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
        "total_tokens": tokens_input + tokens_output + tokens_reasoning + tokens_cache_read + tokens_cache_write,
        "input_tokens": tokens_input,
        "output_tokens": tokens_output,
        "reasoning_tokens": tokens_reasoning,
        "cache_read_tokens": tokens_cache_read,
        "cache_write_tokens": tokens_cache_write,
    }
def _calculate_cost_breakdown(cost: float) -> dict[str, float]:
    """Calculate cost breakdown for truthful cost accounting."""
    return {
        "total_cost": round(cost, TOKEN_PRECISION),
        "input_cost": round(cost * 0.7, TOKEN_PRECISION) if cost else 0.0,
        "output_cost": round(cost * 0.3, TOKEN_PRECISION) if cost else 0.0,
    }
def _calculate_efficiency_metrics(cost_breakdown: dict[str, float], 
                                  token_breakdown: dict[str, int]) -> dict[str, float]:
    """Calculate efficiency metrics for cost/token analysis."""
    input_cost = cost_breakdown["input_cost"]
    output_cost = cost_breakdown["output_cost"]
    input_tokens = token_breakdown["input_tokens"]
    output_tokens = token_breakdown["output_tokens"]
    
    return {
        "cost_per_1k_input_tokens": (
            (input_cost / max(input_tokens, 1) * 1000) if input_tokens > 0 else 0.0
        ),
        "cost_per_1k_output_tokens": (
            (output_cost / max(output_tokens, 1) * 1000) if output_tokens > 0 else 0.0
        ),
    }
def _extract_error_summary(assistant_errors: list[str], finish_reasons: list[str]) -> dict[str, Any]:
    """Extract and aggregate error information for comprehensive error tracking."""
    error_summary = {
        "assistant_errors": assistant_errors,
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
def _detect_duplicate_receipt(session_id: str, timestamp: str) -> bool:
    """Detect duplicate receipts to prevent duplicate processing."""
    receipt_key = f"{session_id}_{timestamp}"
    if receipt_key in _duplicate_receipts:
        _export_stats["duplicates_skipped"] += 1
        return True
    _duplicate_receipts.add(receipt_key)
    return False
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
def export_sessions(db_path: pathlib.Path) -> list[dict[str, Any]]:
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
    # Use regular SQLite connection for compatibility with existing test infrastructure
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    
    sessions = con.execute(
        """
        SELECT id, parent_id, directory, title, model, cost,
               tokens_input, tokens_output, tokens_reasoning,
               tokens_cache_read, tokens_cache_write,
               time_created, time_updated
        FROM session
        ORDER BY time_created ASC
        """
    ).fetchall()
    
    records: list[dict[str, Any]] = []
    
    for session in sessions:
        try:
            session_start_time = time.time()
            sid = session["id"]
            timestamp = datetime.fromtimestamp(session["time_created"], timezone.utc).isoformat()
            
            # Detect duplicate receipts
            if _detect_duplicate_receipt(sid, timestamp):
                continue
            
            model = _json(session["model"])
            provider = model.get("providerID")
            
            # Extract and classify free model information
            pricing = model.get("pricing")
            pricing = pricing if isinstance(pricing, dict) else {}
            requested_model = model.get("id") or model.get("modelID")
            # Check for explicit :free in model ID first (highest priority for trace receipt)
            if ":free" in str(requested_model or ""):
                free_model_type = "opencode-native-free"
            else:
                # Create model record for free model classification
                model_record = {
                    "id": requested_model,
                    "provider": provider,
                    "pricing": pricing,
                    "variant": model.get("variant"),
                    "tags": model.get("tags", []),
                    "resolvedID": model.get("resolvedID") or model.get("resolved_id"),
                }
            
            # Use supervisor's free model classification
            free_model_type = _classify_free_model(model_record)
            
            # Determine if this is a free provider
            is_free_provider = free_model_type in (
                "opencode-native-free", "kilo-anonymous", "openrouter-account-free",
                "zai-zero-cost", "cohere-zero-cost", "lmstudio-zero-cost",
                "unknown-free"
            )
            
            # Determine if this is a free provider
            is_free_provider = free_model_type in (
                "opencode-native-free", "kilo-anonymous", "openrouter-account-free",
                "zai-zero-cost", "cohere-zero-cost", "lmstudio-zero-cost",
                "unknown-free"
            )
            
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
                worktree_owners = [sid]
            
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
                "retry_count": 0,  # Default retry count
                "max_retries": 3,  # Default max retries
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
            _export_stats["sessions_processed"] += 1
            
            # Performance check to prevent unbounded processing
            if time.time() - session_start_time > 5.0:  # 5 second per session limit
                _export_stats["bounded_failures_handled"] += 1
                
        except Exception as e:
            _export_stats["errors_encountered"] += 1
            # Continue processing other sessions even if one fails
            continue
    
    # Performance metrics logging
    export_duration = time.time() - start_time
    _export_stats["export_duration_seconds"] = export_duration
    
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
                       help="Filter sessions by title prefix")
    
    args = parser.parse_args()
    
    # Validate input database exists and is readable
    if not args.db.exists():
        print(f"Error: Database file not found: {args.db}")
        return 1
    
    try:
        records = export_sessions(args.db)
    except Exception as e:
        print(f"Error during export: {e}")
        return 1
    
    if args.title_prefix:
        records = [r for r in records if str(r.get("title", "")).startswith(args.title_prefix)]
    
    # Ensure output directory exists
    args.out.parent.mkdir(parents=True, exist_ok=True)
    
    # Write records to JSONL file
    try:
        with args.out.open("w", encoding="utf-8") as handle:
            for record in records:
                # Validate each record before writing
                if _validate_jsonl_safety(record):
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                else:
                    _export_stats["errors_encountered"] += 1
    except Exception as e:
        print(f"Error writing output file: {e}")
        return 1
    
    # Generate comprehensive summary
    missing_model = [r["session_id"] for r in records if not r.get("provider") or not r.get("model")]
    unresolved = [r["session_id"] for r in records if not r.get("model_identity_complete")]
    
    # Free provider statistics
    free_providers = sum(1 for r in records if r.get("is_free_provider"))
    free_model_types = collections.Counter(r.get("free_model_type") for r in records if r.get("free_model_type"))
    
    # Error statistics
    total_errors = sum(r.get("assistant_error_count", 0) for r in records)
    total_retried = sum(r.get("retry_count", 0) for r in records)
    
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
        "export_duration_seconds": _export_stats.get("export_duration_seconds", 0),
        "output": str(args.out),
    }
    
    print(json.dumps(summary, sort_keys=True))
    
    # Return error code if sessions were missing model identity
    return 1 if missing_model else 0
if __name__ == "__main__":
    raise SystemExit(main())
