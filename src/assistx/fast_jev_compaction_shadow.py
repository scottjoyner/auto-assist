"""Side-effect-free audit of proposed fast-jev-compaction transcripts.

Research fixtures only. This is NOT a compactor, a Jev client, a trace
ingester, or authenticated approval to send prompts to any external provider.
Returns only aggregate advisory status. Original and proposed transcripts stay
in the caller's memory; never stores, exports or logs their contents.
"""
from __future__ import annotations

from functools import lru_cache
import json
from typing import Any

MAX_MESSAGES = 256
MAX_TOOL_PAIRS = 128
MAX_TRANSCRIPT_BYTES = 2_000_000
MAX_HEAD_CHARS = 300
SAFE_COMPACTABLE_TOOL_NAMES = frozenset({"Read", "Grep", "Glob"})
DECISIONS = frozenset({"keep", "drop_result", "drop_call"})
SCOPE = "synthetic_fixture_only"


class ShadowCompactionRejected(ValueError):
    """Fail closed on malformed, ambiguous, or ineligible compaction."""


def _is_text(value: Any) -> bool:
    return type(value) is str


def _read_messages(value: Any, name: str) -> tuple[list[dict], dict, dict, list]:
    if type(value) is not list or not 1 <= len(value) <= MAX_MESSAGES:
        raise ShadowCompactionRejected(f"{name}: malformed or unbounded messages")
    try:
        serialized = json.dumps(value, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ShadowCompactionRejected(f"{name}: non-JSON message") from exc
    if len(serialized.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        raise ShadowCompactionRejected(f"{name}: excessive data")
    calls: dict[str, tuple[int, dict]] = {}
    results: dict[str, tuple[int, dict]] = {}
    signatures = []
    for index, item in enumerate(value):
        if type(item) is not dict or not {"role", "text", "toolUses"} <= set(item) or (
            set(item) - {"role", "text", "toolUses", "toolResults"}
        ):
            raise ShadowCompactionRejected(f"{name}: unrecognized message structure")
        if item["role"] not in ("user", "assistant") or type(item["role"]) is not str:
            raise ShadowCompactionRejected(f"{name}: bad role")
        if not _is_text(item["text"]):
            raise ShadowCompactionRejected(f"{name}: bad message text")
        uses = item["toolUses"]
        output = item.get("toolResults", [])
        if type(uses) is not list or type(output) is not list:
            raise ShadowCompactionRejected(f"{name}: tool arrays must be lists")
        ids_use = []
        ids_result = []
        for tool in uses:
            if type(tool) is not dict or (
                not {"tool_use_id", "tool", "input"} <= set(tool)
                or set(tool) - {"tool_use_id", "tool", "input", "text", "isError"}
            ):
                raise ShadowCompactionRejected(f"{name}: invalid tool-call shape")
            if (not _is_text(tool["tool_use_id"]) or
                    not 1 <= len(tool["tool_use_id"]) <= 128 or
                    not _is_text(tool["tool"]) or not 1 <= len(tool["tool"]) <= 80 or
                    type(tool["input"]) is not dict or
                    ("text" in tool and not _is_text(tool["text"])) or
                    ("isError" in tool and type(tool["isError"]) is not bool)):
                raise ShadowCompactionRejected(f"{name}: invalid tool-call field")
            key = tool["tool_use_id"]
            if key in calls:
                raise ShadowCompactionRejected(f"{name}: duplicate tool use ID")
            calls[key] = (index, tool)
            ids_use.append(key)
        for tool in output:
            if type(tool) is not dict or (
                not {"tool_use_id", "text"} <= set(tool)
                or set(tool) - {"tool_use_id", "text", "isError"}
            ):
                raise ShadowCompactionRejected(f"{name}: invalid result shape")
            if (not _is_text(tool["tool_use_id"]) or
                    not 1 <= len(tool["tool_use_id"]) <= 128 or
                    not _is_text(tool["text"]) or
                    ("isError" in tool and type(tool["isError"]) is not bool)):
                raise ShadowCompactionRejected(f"{name}: invalid result value")
            key = tool["tool_use_id"]
            if key in results:
                raise ShadowCompactionRejected(f"{name}: duplicate tool result ID")
            results[key] = (index, tool)
            ids_result.append(key)
        signatures.append((item["role"], item["text"],
                           frozenset(ids_use), frozenset(ids_result)))
    if len(calls) > MAX_TOOL_PAIRS or set(calls) != set(results):
        raise ShadowCompactionRejected(f"{name}: orphan tool call or tool result")
    for key in calls:
        if results[key][0] < calls[key][0]:
            raise ShadowCompactionRejected(f"{name}: result before tool call")
    return value, calls, results, signatures


def _truncated(original: str, head_chars: int, is_error: bool) -> str:
    if len(original) <= head_chars + 120:
        return original
    prefix = original[:head_chars] + "\n" if head_chars > 0 else ""
    suffix = " (error)" if is_error else ""
    return (
        f"{prefix}[fast-jev-compaction truncated "
        f"{len(original)-head_chars} chars of this tool result"
        f"{suffix}; re-run the tool if needed]"
    )


def _same_tool(before: dict, after: dict, action: str, head_chars: int,
               *, is_result: bool) -> bool:
    if before["tool_use_id"] != after["tool_use_id"]:
        return False
    if not is_result and (before["tool"] != after["tool"] or
                          before["input"] != after["input"]):
        return False
    if before.get("isError", False) != after.get("isError", False):
        return False
    orig_text = before["text"] if is_result else before.get("text", "")
    new_text = after["text"] if is_result else after.get("text", "")
    expected = (_truncated(orig_text, head_chars, before.get("isError", False))
                if action == "drop_result" else orig_text)
    return new_text == expected


def audit_shadow_compaction(
    original: Any,
    proposed: Any,
    *,
    decisions: dict[str, str],
    protected_ids: list[str],
    preserve_recent_messages: int = 6,
    truncate_head_chars: int = 300,
    scope: str = SCOPE,
) -> dict:
    """Validate only synthetic ephemeral outputs from an *offline mocked* Jev.

    decisions and protected_ids are caller-supplied, NOT authenticated. Never
    interpret PASS as permission to compact live traces or publish prompts.
    """
    if scope != SCOPE or type(scope) is not str:
        raise ShadowCompactionRejected("only explicitly synthetic research scope")
    if type(preserve_recent_messages) is not int or not 0 <= preserve_recent_messages <= 64:
        raise ShadowCompactionRejected("invalid recent-message window")
    if type(truncate_head_chars) is not int or not 0 <= truncate_head_chars <= MAX_HEAD_CHARS:
        raise ShadowCompactionRejected("invalid head length")
    if type(decisions) is not dict or type(protected_ids) is not list:
        raise ShadowCompactionRejected("invalid caller-side policy")
    if any(not _is_text(x) for x in decisions) or any(
        type(x) is not str or x not in DECISIONS for x in decisions.values()
    ):
        raise ShadowCompactionRejected("unsupported or non-string decisions")
    if any(not _is_text(x) for x in protected_ids) or len(set(protected_ids)) != len(protected_ids):
        raise ShadowCompactionRejected("invalid protected IDs")
    before, before_calls, before_results, before_signatures = _read_messages(original, "original")
    after, after_calls, after_results, after_signatures = _read_messages(proposed, "proposed")
    if set(decisions) - set(before_calls) or set(protected_ids) - set(before_calls):
        raise ShadowCompactionRejected("unknown original tool ID")
    if set(after_calls) - set(before_calls) or set(after_results) - set(before_results):
        raise ShadowCompactionRejected("new tool calls or results are forbidden")
    # Each output message must correspond to an original message in original
    # order and with identical role/text; calls/results may only disappear.
    # All nonempty original user AND assistant text must survive verbatim.
    at = 0
    matched = []
    for candidate in after_signatures:
        while at < len(before_signatures):
            prior = before_signatures[at]
            at += 1
            if (prior[0:2] == candidate[0:2] and
                    candidate[2].issubset(prior[2]) and
                    candidate[3].issubset(prior[3])):
                matched.append(at-1)
                break
        else:
            raise ShadowCompactionRejected("invented or reordered message")
    matched_set = set(matched)
    if any(i not in matched_set and message["text"].strip()
           for i,message in enumerate(before)):
        raise ShadowCompactionRejected("user or assistant prose was deleted")
    recent_cutoff = max(0,len(before)-preserve_recent_messages)
    auto_protected = set(protected_ids)
    for key,(index,tool) in before_calls.items():
        result_index,result = before_results[key]
        if (index == 0 or index >= recent_cutoff or
                result_index >= recent_cutoff or
                tool["tool"] not in SAFE_COMPACTABLE_TOOL_NAMES or
                tool.get("isError",False) or result.get("isError",False)):
            auto_protected.add(key)
    counts={"kept":0,"result_truncated":0,"calls_dropped":0}
    for key,(_,tool) in before_calls.items():
        action=decisions.get(key,"keep")
        kept=key in after_calls
        if key in auto_protected and action != "keep":
            raise ShadowCompactionRejected("protected evidence cannot be compacted")
        if action == "drop_call":
            if kept or key in after_results:
                raise ShadowCompactionRejected("dropped call still present")
            counts["calls_dropped"]+=1
            continue
        if not kept or key not in after_results:
            raise ShadowCompactionRejected("call/result must remain paired")
        if not _same_tool(tool,after_calls[key][1],action,truncate_head_chars,is_result=False):
            raise ShadowCompactionRejected("tool input or tool text mutated")
        if not _same_tool(before_results[key][1],after_results[key][1],
                          action,truncate_head_chars,is_result=True):
            raise ShadowCompactionRejected("result text or status mutated")
        if action=="keep":
            counts["kept"]+=1
        else:
            counts["result_truncated"]+=1
    before_size=len(json.dumps(before,ensure_ascii=False,separators=(",",":")).encode("utf-8"))
    after_size=len(json.dumps(after,ensure_ascii=False,separators=(",",":")).encode("utf-8"))
    if after_size > before_size:
        raise ShadowCompactionRejected("compact proposal increased serialized bytes")
    return {
        "kind":"fast_jev_compaction_shadow_audit_v1",
        "scope":SCOPE,
        "upstream_tested_commit":"e3f262a7f4d42bd8dd32ced30d26176f7cb545b0",
        "original_message_count":len(before),
        "compacted_message_count":len(after),
        "original_tool_pair_count":len(before_calls),
        "protected_tool_pair_count":len(auto_protected),
        "actions":counts,
        "original_serialized_bytes":before_size,
        "proposed_serialized_bytes":after_size,
        "synthetic_bytes_reduced":before_size-after_size,
        "identical_user_and_assistant_prose":True,
        "all_remaining_tool_pairs_intact":True,
        "protected_evidence_preserved":True,
        "real_token_savings_measured":False,
        "external_jev_provider_called":False,
        "trusted_custody_verified":False,
        "authenticated_operator_authorization_verified":False,
        "live_trace_compaction_allowed":False,
        "production_gate":"HOLD",
    }
