"""After separately observed held Bolt read, cap=3 must still reject."""
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

p = json.loads(Path("/tmp/worker-config.json").read_text())
req = Request(
    "http://" + p["broker_host"] + ":8808/v1/plan",
    data=json.dumps({"plan_id": "get_task_read",
                     "parameters": {"id": p["task_id"]}}).encode(),
    method="POST", headers={
        "Authorization": "Bearer " + p["bearer"],
        "Idempotency-Key": "research-fourth-after-held-bolt-0004",
        "Content-Type": "application/json",
    })
try:
    with urlopen(req, timeout=7) as response:
        status = response.status
except HTTPError as exc:
    status = exc.code
assert status == 503, status
print(json.dumps({"fourth_request_denied_after_observed_bolt": True}))
