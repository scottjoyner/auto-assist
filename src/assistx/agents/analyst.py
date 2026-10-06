import json
import os
import subprocess
import sys
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
    Executes analysis code in an isolated subprocess sandbox.
    Captures stdout emitted by the user code and returns JSON-compatible results.
    Expects a function main(rows)->dict.
    """
    timeout_s = float(os.getenv("ANALYSIS_TIMEOUT_S", "8"))
    cmd = [sys.executable, "-m", "assistx.sandbox_runner"]
    payload = json.dumps({"code": code, "rows": rows}, ensure_ascii=False).encode("utf-8")
    try:
        proc = subprocess.run(
            cmd,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1.0, timeout_s + 1.0),
            check=False,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Analysis sandbox timed out after {timeout_s}s") from exc

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Analysis sandbox failed (exit={proc.returncode}): {err}")

    try:
        data = json.loads(proc.stdout.decode("utf-8"))
    except Exception as exc:
        out = proc.stdout.decode("utf-8", errors="replace")
        raise RuntimeError(f"Analysis sandbox returned invalid JSON: {out[:400]}") from exc

    result = data.get("result")
    stdout = data.get("stdout", "")
    if not isinstance(result, dict):
        raise RuntimeError("Analysis main(rows) must return a dict result")
    return result, stdout
