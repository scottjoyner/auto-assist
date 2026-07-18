
from __future__ import annotations
from typing import Dict, Any, List
from langgraph.graph import StateGraph, END
from pydantic import BaseModel
from ..ollama_llm import json_chat
from ..llm.client import last_reasoning_content
from ..tools.web_search import web_search
from ..tools.python_exec import run_python
from ..tools.files import write_text
from ..neo4j_client import Neo4jClient
from ..policy import tool_allowed
from ..logging_utils import get_logger
from ..metrics import TOOL_CALLS, TOOL_LATENCY, LLM_TOKENS

import json, time
from datetime import datetime, date, time as dtime

logger = get_logger()


def _json_safe(value: Any) -> Any:
    """Recursively convert values that are not JSON-serializable (Neo4j
    DateTime, datetime/date/time, bytes) into strings so agent run results
    can be stored by RQ without raising ``... is not JSON serializable``."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace")
        except Exception:
            return repr(value)
    # Neo4j driver DateTime / Duration etc. expose isoformat()
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)

TOOLS = {
    "web_search": {"desc": "Search the web for facts, URLs, and recent info.", "schema": {"query": "str", "max_results": "int?"}, "func": lambda args: web_search(args.get("query", ""), int(args.get("max_results", 5)))},
    "python": {"desc": "Execute small Python snippets in a sandbox and return the result variable.", "schema": {"code": "str", "input_json": "dict?"}, "func": lambda args: run_python(args.get("code", "result=None"), args.get("input_json"))},
    "write_text": {"desc": "Write a text file to disk and return its path + sha256.", "schema": {"path": "str", "text": "str"}, "func": lambda args: write_text(args.get("path"), args.get("text", ""))},
}

class AgentState(BaseModel):
    task: Dict[str, Any]
    history: List[Dict[str, Any]] = []
    result: Dict[str, Any] | None = None
    done: bool = False

_def_ratio = 4
def _estimate_tokens(text: str) -> int:
    if not text: return 0
    return max(1, len(text) // _def_ratio)

DECIDE_PROMPT = """
You are an execution agent. You are given a task JSON. You may call one tool per step from the catalog below.
Respond ONLY in JSON with one of:
- {"tool":"<name>", "input":{...}, "reason":"..."}
- {"final": {"result":{...}, "summary":"..."}}
Tools:
__TOOLS__
Task:
__TASK__
"""

def decide(state: AgentState) -> AgentState:
    tools_desc = "\n".join([f"- {k}: {v['desc']} schema={v['schema']}" for k, v in TOOLS.items()])
    prompt = DECIDE_PROMPT.replace("__TOOLS__", tools_desc).replace("__TASK__", json.dumps(_json_safe(state.task), ensure_ascii=False))
    j = json_chat(prompt, schema_hint="AgentDecision")
    reasoning = last_reasoning_content()
    state.history.append({"decision": j, "reasoning": reasoning} if reasoning else {"decision": j})
    logger.info(f"decision for task {state.task.get('id','?')}: {j}")
    if not isinstance(j, dict):
        state.history.append({"error": f"malformed decision (not an object): {type(j).__name__}"})
        state.done = True
        return state
    if "final" in j:
        state.result = j["final"]
        state.done = True
    elif "tool" in j:
        tname = j["tool"]
        args = j.get("input", {})
        ok, reason = tool_allowed(tname, args)
        if not ok:
            state.history.append({"policy_denied": {"tool": tname, "reason": reason}})
            state.done = True
            return state
        tool = TOOLS.get(tname)
        if not tool:
            state.history.append({"error": f"unknown tool {tname}"})
            state.done = True
        else:
            t0 = time.time()
            out = tool["func"](args)
            dt = time.time() - t0
            TOOL_LATENCY.labels(tool=tname).observe(dt)
            TOOL_CALLS.labels(tool=tname, ok=str(True)).inc()
            usage = {"input_tokens_est": _estimate_tokens(json.dumps(args)), "output_tokens_est": _estimate_tokens(json.dumps(out))}
            if isinstance(out, dict):
                out_with_usage = dict(out); out_with_usage.setdefault("usage", usage)
            else:
                out_with_usage = {"result": out, "usage": usage}
            state.history.append({"tool": tname, "input": args, "output": out_with_usage})
    else:
        state.done = True
    return state

def should_continue(state: AgentState) -> str:
    if state.done or len(state.history) >= 8:
        return END
    return "decide"

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("decide", decide)
    g.add_conditional_edges("decide", should_continue)
    g.set_entry_point("decide")
    return g.compile()

def run_task(neo: Neo4jClient, task: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    import os as _os
    graph = build_graph()
    state = AgentState(task=task)
    rid = neo.create_run(task_id=task["id"], agent="LangGraphExecutor", model="ollama:" + task.get("model", "auto"), manifest={"begin": task})
    # Hard wall-clock guard so a single task can never wedge a worker forever.
    task_budget_s = float(_os.getenv("TASK_WALLCLOCK_S", "600"))
    deadline = time.time() + task_budget_s
    try:
        if dry_run:
            preview = decide(state)
            neo.log_tool_call(run_id=rid, tool="preview", input_json=task, output_json=preview.model_dump(), ok=True)
            neo.complete_run(rid, "DONE", result_json={"history": state.history, "result": state.result})
            out = preview.model_dump(); out["run_id"] = rid
            return out
        max_steps = 8
        while True:
            last_len = len(state.history)
            # LangGraph returns an AddableValuesDict, not our pydantic AgentState,
            # so re-hydrate it after each invoke.
            result = graph.invoke(state.model_dump())
            state = AgentState(**{**state.model_dump(), **dict(result)})
            if len(state.history) > last_len:
                step = state.history[-1]
                if "tool" in step:
                    neo.log_tool_call(rid, step["tool"], step.get("input", {}), step.get("output", {}), True)
                if "policy_denied" in step:
                    neo.log_tool_call(rid, "policy_denied", step["policy_denied"], {}, False)
            # Terminate when the task is marked done OR the agent graph can no
            # longer make progress (entry guard caps history at 8 steps). Without
            # this the outer loop would spin forever doing nothing.
            if state.done or len(state.history) >= max_steps or len(state.history) == last_len:
                if not state.done:
                    state.done = True
                neo.complete_run(rid, "DONE", result_json={"history": state.history, "result": state.result})
                break
            # Wall-clock guard: stop the loop if we've blown the task budget so a
            # slow/hanging LLM or tool call can't wedge the worker indefinitely.
            if time.time() > deadline:
                logger.warning("run_task budget exceeded for task %s", task.get("id"))
                state.done = True
                neo.complete_run(rid, "DONE", result_json={"history": state.history, "result": state.result})
                break
        # Log artifacts for write_text outputs
        for step in state.history:
            if step.get('tool') == 'write_text':
                out = step.get('output', {})
                p = out.get('path')
                sha = out.get('sha256')
                if isinstance(p, str):
                    neo.log_artifact(rid, kind='file', path=p, sha256=sha)
        ret = state.model_dump(); ret["run_id"] = rid
        return _json_safe(ret)
    except Exception as e:
        neo.complete_run(rid, "FAILED", result_json={"history": state.history, "error": str(e)})
        neo.log_tool_call(rid, "error", {"task": task}, {"error": str(e)}, False)
        raise
