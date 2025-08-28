
from __future__ import annotations
import tempfile, subprocess, textwrap, os, resource, json
from typing import Dict, Any
from ..config import settings

def _limit_resources():
    resource.setrlimit(resource.RLIMIT_CPU, (settings.py_max_seconds, settings.py_max_seconds))
    mem = settings.py_max_mem_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))

def run_python(code: str, input_json: Dict[str, Any] | None = None) -> Dict[str, Any]:
    wrapper = f"""
import json, sys
inp = json.loads(sys.stdin.read()) if not sys.stdin.closed else None
# user code starts
{code}
# user code may set `result` variable
out = locals().get('result', None)
print(json.dumps({{'result': out}}, ensure_ascii=False))
"""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "prog.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(wrapper))
        cmd = ["python3", path]
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=_limit_resources, text=True)
            out, err = p.communicate(input=(json.dumps(input_json) if input_json else ""), timeout=settings.py_max_seconds + 5)
            ok = p.returncode == 0
        except subprocess.TimeoutExpired:
            p.kill()
            return {"ok": False, "error": "timeout"}
    try:
        payload = json.loads(out.strip()) if out.strip() else {"result": None}
    except Exception:
        payload = {"result_raw": out}
    payload.update({"stderr": err, "ok": ok})
    return payload
