"""Run inside the final image with networking disabled, not on fleet nodes."""
import importlib.metadata
import json
import subprocess
import sys


def verify(app, hermes):
    if app["prefix"] == hermes["prefix"]:
        raise ValueError("Hermes must have an independent interpreter")
    if app["dotenv"] != "1.0.1" or app["pydantic"] != "2.8.2":
        raise ValueError("Hermes installation changed application dependency pins")
    if hermes["version"] != "0.19.0":
        raise ValueError("Unexpected Hermes runtime version")


if __name__ == "__main__":
    app = {
        "prefix": sys.prefix,
        "dotenv": importlib.metadata.version("python-dotenv"),
        "pydantic": importlib.metadata.version("pydantic"),
    }
    code = "import sys,json,importlib.metadata as m; print(json.dumps({'prefix':sys.prefix,'version':m.version('hermes-agent')}))"
    result = subprocess.run(["/opt/hermes/bin/python", "-c", code], check=True, capture_output=True, text=True, timeout=30)
    verify(app, json.loads(result.stdout))
    print("PASS: independent application and Hermes runtime dependencies")
