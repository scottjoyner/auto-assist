import io, contextlib, json
from typing import List, Dict, Any, Tuple
from .llm import tool_json

SYSTEM_ANALYST = """You are a data analyst who writes concise, correct Python to compute
the metrics/tables needed to answer the user's question from a list of result rows.

Input:
- question: natural language
- rows: list of dicts (from Neo4j result)
Write a Python function main(rows) that returns a dict 'result' and optionally 'table' (list of dicts).
Rules:
- Use only Python stdlib + pandas if beneficial.
- Do NOT read/write files or do network I/O.
- Keep it under ~60 lines.
Respond JSON: {"code":"<python source>", "notes":"brief rationale"}."""

def generate_analysis_code(question: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    msg = [
        {"role":"system", "content": SYSTEM_ANALYST},
        {"role":"user", "content": json.dumps({"question": question, "rows": rows}, ensure_ascii=False)}
    ]
    return tool_json(msg)

def run_user_code(code: str, rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
    """
    Executes user code safely-ish. Provides 'rows' and 'pd' in locals.
    Captures stdout. Expects a function main(rows)->dict.
    """
    import pandas as pd
    safe_globals = {"__builtins__": {"len": len, "range": range, "min": min, "max": max, "sum": sum, "sorted": sorted}}
    env = {"rows": rows, "pd": pd}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, safe_globals, env)
        if "main" not in env:
            raise RuntimeError("No main(rows) found")
        result = env["main"](rows)
    return result, out.getvalue()
