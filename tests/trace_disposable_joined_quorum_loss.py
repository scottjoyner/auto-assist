"""Physical negative: joined broker must reject fresh graph read on etcd minority.

Runs only in scoped disposable worker container after orchestrator stops
TWO disposable etcd voter containers. Never runs against production etcd.
"""
from __future__ import annotations
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
config=json.loads(Path("/tmp/worker-config.json").read_text())
request=Request(
    "http://"+config["broker_host"]+":8808/v1/plan",
    method="POST",
    data=json.dumps({"plan_id":"get_task_read","parameters":{
        "id":config["task_id"]}}).encode(),
    headers={
        "Content-Type":"application/json",
        "Authorization":"Bearer "+config["bearer"],
        "Idempotency-Key":"research-fresh-on-etcd-minority-0005",
    },
)
try:
    with urlopen(request,timeout=9) as response:
        status=response.status
        body=json.loads(response.read(2048))
except HTTPError as exc:
    status=exc.code
    body=json.loads(exc.read(2048))
assert status==503,(status,body)
assert body=={"error":"ADMISSION_UNCERTAIN"},body
print(json.dumps({
    "disposable_two_voter_outage_denies_fresh_broker_read":True,
    "http_result":"503_ADMISSION_UNCERTAIN",
    "graph_read_after_loss_not_observed":True,
    "does_not_prove_neo4j_side_fence":True,
    "production_authority":False,
},sort_keys=True))
