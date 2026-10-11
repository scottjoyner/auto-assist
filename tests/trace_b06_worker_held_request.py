"""Third distinct request, held as an explicit broker-owned Bolt transaction."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

conf = json.loads(Path("/tmp/worker-config.json").read_text())
req = Request(
    "http://" + conf["broker_host"] + ":8808/v1/plan",
    data=json.dumps({"plan_id": "get_task_read",
                     "parameters": {"id": conf["task_id"]}}).encode(),
    method="POST",
    headers={
        "Authorization": "Bearer " + conf["bearer"],
        "Content-Type": "application/json",
        "Idempotency-Key": "research-third-held-bolt-request-0003",
        "X-B06-Explicit-Bolt": "1",
    },
)
try:
    with urlopen(req, timeout=38) as reply:
        status, body = reply.status, json.loads(reply.read(8192))
except HTTPError as err:
    status, body = err.code, json.loads(err.read(8192))
assert status == 200, (status, body)
assert body == {"task": {"id": conf["task_id"], "status": "READY"}}
print(json.dumps({"held_bolt_request_finished": True, "http_status": status}))
