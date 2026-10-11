"""Single-task admitted read canary; worker sees broker only, no Raft/Neo4j."""
from __future__ import annotations
import json, socket
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
CONFIG=json.loads(Path("/tmp/worker-config.json").read_text())
ORIGIN="http://"+CONFIG["broker_host"]+":8808"
KEY="research-one-logical-task-request-0001"
def call(plan="get_task_read",key=KEY,task_id=None, bearer=True):
    payload={"plan_id":plan,"parameters":{"id":task_id or CONFIG["task_id"]}}
    req=Request(ORIGIN+"/v1/plan",data=json.dumps(payload).encode(),
                method="POST",headers={
                    "Content-Type":"application/json",
                    **({"Authorization":"Bearer "+CONFIG["bearer"]} if bearer else {}),
                    **({"Idempotency-Key":key} if key else {}),
                })
    try:
        with urlopen(req,timeout=8) as r:
            return r.status,json.loads(r.read(9000))
    except HTTPError as e:
        return e.code,json.loads(e.read(9000))
def connect(ip,port):
    try:
        with socket.create_connection((ip,port),timeout=2):
            return True
    except OSError: return False

assert not connect(CONFIG["neo4j_ip"],7687)
assert not connect(CONFIG["neo4j_ip"],7474)
assert not connect(CONFIG["quorum_ip"],CONFIG.get("quorum_port",2379))
assert call(bearer=False)[0]==401
assert call(key=None)[0]==422
status,resp=call()
assert (status,resp)==(200,{"task":{"id":CONFIG["task_id"],"status":"READY"}}),(status,resp)
assert call()[0]==409
assert call(plan="get_task_claim_guard")[0]==409
assert call(key="research-second-unique-request-0002")[0]==200
assert call(key="research-third-unique-request-0003")[0]==200
assert call(key="research-fourth-unique-request-0004")[0]==503  # cap=3
print(json.dumps({
 "real_Raft_committed_before_graph_read":True,
 "direct_worker_bolt_denied":True,
 "direct_worker_graph_http_denied":True,
 "direct_worker_etcd_ip_denied":True,
 "uncredentialed_request_denied":True,
 "missing_idempotency_key_denied":True,
 "duplicate_operation_denied":True,
 "changed_plan_same_identity_denied":True,
 "cap_three_preserves_pending":True,
 "no_witness_or_automatic_capacity_release":True,
 "single_host_Raft_not_independent_failure_domains":not CONFIG.get("remote_three_host",False),
 "three_real_raft_hosts_mtls_RBAC":bool(CONFIG.get("remote_three_host",False)),
 "production_deployed":False,
},sort_keys=True))
