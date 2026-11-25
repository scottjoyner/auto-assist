#!/usr/bin/env python3
"""Research-only exact-source Cypher race harness, fixed host/image, default plan-only.
Native startup currently failed twice. Do not treat as an acceptance witness.
No production graph, host ports, bind mounts, inference, or existing containers.
"""
import argparse, ast, concurrent.futures, json, subprocess, time, sys
from pathlib import Path
source=Path(__file__).resolve().parents[1]/'src/assistx/neo4j_client.py'
m=ast.parse(source.read_text());f=next(x for x in ast.walk(m) if isinstance(x,ast.FunctionDef) and x.name=='complete_task'); q=next(x.value for x in ast.walk(f) if isinstance(x,ast.Constant) and isinstance(x.value,str) and 'CREATE (r:AgentRun' in x.value)
ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=3','deathstar']
name='assistx-completion-fence-disposable-20261125'
image='sha256:8ccf5ebc4812295e1cc59ad9ce2fa9f1d8a4d167cbb8198c716f09d5a848cfec'
def remote(cmd,data=None,timeout=20):
 p=subprocess.run(ssh+[cmd],input=data,text=True,capture_output=True,timeout=timeout)
 if p.returncode: raise RuntimeError('remote exit '+str(p.returncode)+' '+p.stderr[-400:])
 return p.stdout.strip()
def c(stmt):return remote(f'docker exec -e JAVA_TOOL_OPTIONS="-Xms16m -Xmx96m" -i {name} cypher-shell --format plain',stmt+'\n',25)
parser=argparse.ArgumentParser(description='Disposable Deathstar native Cypher race test; no production graph or model requests')
parser.add_argument('--run',action='store_true',help='Explicitly opt into a capped disposable graph')
if not parser.parse_args().run:
 print('PLAN_ONLY: no container or network calls');sys.exit(0)
if name in remote('docker ps -a --format "{{.Names}}"').splitlines():raise RuntimeError('existing container; refuse to touch it')
# Recheck live resources immediately before starting the isolated non-model test.
mem=remote("awk '/MemAvailable:/{print int($2/1024)}' /proc/meminfo; awk '/SwapFree:/{print int($2/1024)}' /proc/meminfo").splitlines();
if len(mem)!=2 or int(mem[0])<12288 or int(mem[1])<2048:raise RuntimeError('remote resource preflight held')
cid=remote(f'docker run --pull never -d --name {name} --network none --memory 2g --memory-swap 2g --cpus 2 --pids-limit 192 --security-opt no-new-privileges -e NEO4J_AUTH=none -e NEO4J_ACCEPT_LICENSE_AGREEMENT=yes -e NEO4J_server_memory_heap_initial__size=128m -e NEO4J_server_memory_heap_max__size=384m -e NEO4J_server_memory_pagecache_size=128m {image}',timeout=30)
print('STARTED',cid[:12],flush=True)
try:
 for _ in range(25):
  if remote(f'docker inspect {cid} --format "{{{{.State.Running}}}}"')!='true':
   print('STARTUP_LOG',remote(f'docker logs --tail 15 {cid}'),flush=True);raise RuntimeError('container exited')
  try:
   if '1' in c('RETURN 1 AS ready;'):break
  except (RuntimeError,subprocess.TimeoutExpired):pass
  time.sleep(1)
 else:raise RuntimeError('readiness deadline')
 print('READY',flush=True)
 def write(i,trial):
  params={'task_id':f'task-race-{trial}','agent_id':'worker-a','completion_id':'same-key','claim_id':'claim-a','session_id':'session-a','status':'DONE','summary':'synthetic','result_json':'{}','run_id':f'run-{trial}-{i}'}
  # cypher-shell :param map; values are fixed synthetic strings.
  param=':param {'+', '.join(k+': '+json.dumps(v) for k,v in params.items())+'};\n'
  return c(param+q.strip()+';')
 results=[]
 for trial in range(3):
  c("CREATE (:Task {id:'task-race-"+str(trial)+"',execution_attempt:1,claimed_by:'worker-a',claim_id:'claim-a',agent_session_id:'session-a',status:'RUNNING',lease_expires_at_ts:timestamp()+600000});")
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool: list(pool.map(lambda i:write(i,trial),range(3)))
  verdict=c("MATCH (t:Task {id:'task-race-"+str(trial)+"'}) OPTIONAL MATCH (t)-[:EXECUTED_BY]->(r:AgentRun) RETURN count(r) AS runs;")
  print('TRIAL',trial+1,verdict.replace('\n',' '),flush=True);results.append(int(verdict.splitlines()[-1]))
 print(json.dumps({'schema':'assistx-native-completion-race-v1','image':image,'trial_counts':results,'concurrent_writers_per_trial':3,'network':'none','production_graph_used':False,'result':'PASS' if results==[1,1,1] else 'RACE_DETECTED'}),flush=True)
 if results != [1,1,1]:raise RuntimeError('duplicate or missing completion run')
finally:
 remote('docker rm -f -v '+cid,timeout=20); print('DISPOSABLE_REMOVED',flush=True)
