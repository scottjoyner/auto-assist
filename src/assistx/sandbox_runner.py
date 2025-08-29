import os, sys, json, io, contextlib, resource, signal

TIMEOUT_S = float(os.getenv("ANALYSIS_TIMEOUT_S", "8"))
MEM_MB = int(os.getenv("ANALYSIS_MEM_MB", "512"))

SAFE_BUILTINS = {
    "len": len, "range": range, "min": min, "max": max, "sum": sum,
    "sorted": sorted, "enumerate": enumerate, "zip": zip, "abs": abs,
    "round": round, "any": any, "all": all, "list": list, "dict": dict, "set": set, "tuple": tuple,
}

ALLOW_IMPORTS = {"pandas", "math", "statistics"}

def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOW_IMPORTS:
        raise ImportError(f"Import not allowed: {name}")
    return __import__(name, globals, locals, fromlist, level)

def limit_resources():
    # CPU time & address space
    cpu = int(TIMEOUT_S) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    mem = MEM_MB * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except Exception:
        pass
    # file descriptors to a small number
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))

def main():
    # read payload from stdin
    payload = json.loads(sys.stdin.read())
    code = payload["code"]
    rows = payload["rows"]

    import pandas as pd

    # sandbox env
    g = {"__builtins__": SAFE_BUILTINS.copy()}
    g["__builtins__"]["__import__"] = safe_import
    # disable open
    def _no_open(*args, **kwargs): raise PermissionError("file I/O disabled")
    g["__builtins__"]["open"] = _no_open

    l = {"rows": rows, "pd": pd}

    limit_resources()

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, g, l)
        if "main" not in l:
            raise RuntimeError("No main(rows) found")
        result = l["main"](rows)

    print(json.dumps({"result": result, "stdout": out.getvalue()}, ensure_ascii=False))

if __name__ == "__main__":
    main()
