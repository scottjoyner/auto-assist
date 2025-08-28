
from __future__ import annotations
from typing import Dict, Any, List
from langgraph.graph import StateGraph, END
from pydantic import BaseModel
from ..ollama_llm import json_chat
from ..tools.web_search import web_search
from ..tools.python_exec import run_python
from ..tools.files import write_text
from ..neo4j_client import Neo4jClient
from ..policy import tool_allowed
from ..logging_utils import get_logger

logger = get_logger()

TOOLS = {
    "web_search": {
        "desc": "Search the web for facts, URLs, and recent info.",
        "schema": {"query": "str", "max_results": "int?"},
        "func": lambda args: web_search(args.get("query", ""), int(args.get("max_results", 5))),
    },
    "python": {
        "desc": "Execute small Python snippets in a sandbox and return the result variable.",
        "schema": {"code": "str", "input_json": "dict?"},
        "func": lambda args: run_python(args.get("code", "result=None"), args.get("input_json")),
    },
    "write_text": {
        "desc": "Write a text file to disk and return its path + sha256.",
        "schema": {"path": "str", "text": "str"},
        "func": lambda args: write_text(args.get("path"), args.get("text", "")),
    },
}

class AgentState(BaseModel):
    task: Dict[str, Any]
    history: List[Dict[str, Any]] = []
    result: Dict[str, Any] | None = None
    done: bool = False

DECIDE_PROMPT = """
You are an execution agent. You are given a task JSON. You may call one tool per step from the catalog below.
Respond ONLY in JSON with one of:
- {"tool":"<name>", "input":{...}, "reason":"..."}
- {"final": {"result":{...}, "summary":"..."}}
Tools:
{tools}
Task:
{task}
"""

def decide(state: AgentState) -> AgentState:
    tools_desc = "\n".join([f"- {k}: {v['desc']} schema={v['schema']}" for k, v in TOOLS.items()])
    j = json_chat(DECIDE_PROMPT.format(tools=tools_desc, task=state.task), schema_hint="AgentDecision")
    state.history.append({"decision": j})
    logger.info(f"decision for task {state.task.get('id','?')}: {j}")
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
            out = tool["func"](args)
            state.history.append({"tool": tname, "input": args, "output": out})
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
    g.add_edge("decide", should_continue)
    g.set_entry_point("decide")
    return g.compile()

def run_task(neo: Neo4jClient, task: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    graph = build_graph()
    state = AgentState(task=task)
    rid = neo.create_run(task_id=task["id"], agent="LangGraphExecutor", model="ollama:" + task.get("model", "auto"), manifest={"begin": task})

    try:
        if dry_run:
            preview = decide(state)
            neo.log_tool_call(run_id=rid, tool="preview", input_json=task, output_json=preview.model_dump(), ok=True)
            neo.complete_run(rid, "DONE")
            return preview.model_dump()

        while True:
            last_len = len(state.history)
            state = graph.invoke(state)
            if len(state.history) > last_len:
                step = state.history[-1]
                if "tool" in step:
                    neo.log_tool_call(rid, step["tool"], step.get("input", {}), step.get("output", {}), True)
                if "policy_denied" in step:
                    neo.log_tool_call(rid, "policy_denied", step["policy_denied"], {}, False)
            if state.done:
                neo.complete_run(rid, "DONE")
                break
        return state.model_dump()
    except Exception as e:
        neo.complete_run(rid, "FAILED")
        neo.log_tool_call(rid, "error", {"task": task}, {"error": str(e)}, False)
        raise
