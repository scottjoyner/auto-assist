import ast
import contextlib
import io
import json
import os
import resource
import socket
import sys

TIMEOUT_S = float(os.getenv("ANALYSIS_TIMEOUT_S", "8"))
MEM_MB = int(os.getenv("ANALYSIS_MEM_MB", "512"))

SAFE_BUILTINS = {
    "len": len, "range": range, "min": min, "max": max, "sum": sum,
    "sorted": sorted, "enumerate": enumerate, "zip": zip, "abs": abs,
    "round": round, "any": any, "all": all, "list": list, "dict": dict,
    "set": set, "tuple": tuple, "print": print,
}

ALLOW_IMPORTS = {"pandas", "math", "statistics"}


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOW_IMPORTS:
        raise ImportError(f"Import not allowed: {name}")
    return __import__(name, globals, locals, fromlist, level)


def _current_virtual_memory_bytes() -> int:
    """Return the current process address-space footprint on Linux.

    RLIMIT_AS is an address-space limit, not an RSS limit. Using a fixed 512 MiB
    absolute cap after Python/native libraries are already mapped can put the
    process *over* its new ceiling immediately on some runners. The sandbox
    budget therefore applies as headroom above the initialized process image.
    """
    try:
        pages = int(open("/proc/self/statm", encoding="ascii").read().split()[0])
        return pages * int(os.sysconf("SC_PAGE_SIZE"))
    except Exception:
        return 0


def limit_resources():
    cpu = int(TIMEOUT_S) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))

    allowance = max(32, MEM_MB) * 1024 * 1024
    baseline = _current_virtual_memory_bytes()
    target = baseline + allowance if baseline else allowance
    try:
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            target = min(target, hard)
            resource.setrlimit(resource.RLIMIT_AS, (target, hard))
        else:
            resource.setrlimit(resource.RLIMIT_AS, (target, target))
    except Exception:
        pass

    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def _uses_pandas(code: str) -> bool:
    """Avoid importing a heavy native stack for code that does not use it."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "pd":
            return True
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "pandas" for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "pandas":
            return True
    return False


def _pandas_binding(code: str):
    if not _uses_pandas(code):
        return None
    try:
        import pandas as pd
        return pd
    except ModuleNotFoundError:
        class _PandasShim:
            class DataFrame(list):
                def __init__(self, *args, **kwargs):
                    data = args[0] if args else kwargs.get("data", [])
                    super().__init__(data if isinstance(data, list) else [data])

            def __getattr__(self, name):
                raise AttributeError(f"pandas is unavailable in this sandbox ({name})")

        return _PandasShim()


def main():
    payload = json.loads(sys.stdin.read())
    code = payload["code"]
    rows = payload["rows"]

    # Load optional heavy dependencies before setting the address-space budget,
    # then give user code a fixed amount of additional headroom.
    pd = _pandas_binding(code)
    limit_resources()

    g = {"__builtins__": SAFE_BUILTINS.copy()}
    g["__builtins__"]["__import__"] = safe_import

    def _no_open(*args, **kwargs):
        raise PermissionError("file I/O disabled")

    g["__builtins__"]["open"] = _no_open

    def _no_socket(*args, **kwargs):
        raise PermissionError("network disabled")

    socket.socket = _no_socket

    l = {"rows": rows}
    if pd is not None:
        l["pd"] = pd

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, g, l)
        if "main" not in l:
            raise RuntimeError("No main(rows) found")
        result = l["main"](rows)

    print(json.dumps({"result": result, "stdout": out.getvalue()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
