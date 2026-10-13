"""Seven-role network-denial research against isolated disposable containers.

Uses *existing* fixture worker plus six plain Python role stand-ins.
These are NOT the real service images or a production-network proof.
No graph query, credentials, or live containers are accessed here.
"""
from __future__ import annotations
import json

ACTOR_ROLES = (
    "assistx-api", "assistx-hermes-adapter", "assistx-redis",
    "auto-assign", "git-redis-1", "git-router-1",
)
ALL_ROLE_NAMES = frozenset(("assistx-worker", *ACTOR_ROLES))
SOCKET_CHECK = """
import json, socket, sys
graph, broker = sys.argv[1:]
def open_tcp(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False
print(json.dumps({
    "graph_ip_bolt":open_tcp(graph,7687),
    "graph_ip_http":open_tcp(graph,7474),
    "graph_dns_bolt":open_tcp("neo4j",7687),
    "graph_dns_http":open_tcp("neo4j",7474),
    "broker_tcp":open_tcp(broker,8808),
}))
"""
def verify_results(sample: dict) -> bool:
    if type(sample) is not dict or set(sample) != {
        "graph_ip_bolt","graph_ip_http","graph_dns_bolt",
        "graph_dns_http","broker_tcp"}:
        raise RuntimeError("INCOMPLETE_SYNTHETIC_ACTOR_PROBE")
    if any(sample[x] is not False for x in sample if x!="broker_tcp"):
        raise RuntimeError("DISPOSABLE_GRAPH_NETWORK_BYPASS")
    if sample["broker_tcp"] is not True:
        raise RuntimeError("DISPOSABLE_BROKER_UNREACHABLE")
    return True

def run_synthetic_peers(*, docker, assert_disposable, made_containers,
                        worker_net: str, graph_ip: str, broker: str,
                        tag: str, python_image: str) -> dict:
    results={}
    for role in ACTOR_ROLES:
        name="assistx-b01-fixture-"+role+"-"+tag
        docker("create","--pull","never","--name",name,
               "--network",worker_net,"--memory","96m",
               "--cpus","0.5","--pids-limit","64",
               python_image,"sleep","180",timeout=18)
        made_containers.append(name)
        docker("start",name)
        assert_disposable(name,{worker_net})
        outcome=docker("exec",name,"python","-c",SOCKET_CHECK,
                       graph_ip,broker,timeout=14,check=False)
        if outcome.returncode!=0:
            raise RuntimeError("DISPOSABLE_ROLE_SOCKET_PROBE_FAILED:"+role)
        data=json.loads(outcome.stdout.strip())
        verify_results(data)
        results[role]={"direct_graph_bolt_denied":True,
                       "direct_graph_http_denied":True,
                       "graph_hostname_denied":True,
                       "broker_socket_reachable":True}
    return results