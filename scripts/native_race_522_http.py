"""Fixed disposable 5.22 race witness. Controller-invoked only on Deathstar; standalone CLI is plan-only.
Query is injected from exact reviewed source by the controller, not arbitrary input.
"""
import json,subprocess,time,re,concurrent.futures,threading,hashlib,socket,sys
NAME='assistx-native-race-522-http-20261125'
IMAGE='sha256:468584de930e2da70f06a23c9faa19fe85c14b18cca4ac6b2c888fe9d7d7578e'
def run(query):
 out={'schema':'assistx-native-race-522-v1','state':'hold','productionGraphUsed':False,'modelRequestsSent':0,'image':IMAGE,'network':'none','querySha256':hashlib.sha256(query.encode()).hexdigest(),'trials':[],'cleanupVerified':False}
 def cmd(a,timeout=10,stdin=None):return subprocess.run(a,text=True,input=stdin,capture_output=True,timeout=timeout)
 cid=None
 def c(stmt,params=None):
  body=json.dumps({'statements':[{'statement':stmt,'parameters':params or {}}]})
  p=cmd(['docker','exec','-i',cid,'curl','--noproxy','*','--fail','--silent','--show-error','--max-time','12','-H','Content-Type: application/json','--data-binary','@-','http://127.0.0.1:7474/db/neo4j/tx/commit'],18,body)
  if p.returncode:raise RuntimeError('http-client-exit-'+str(p.returncode)+':'+p.stderr[-400:])
  if len(p.stdout)>65536:raise ValueError('response limit')
  d=json.loads(p.stdout)
  if d.get('errors'):raise RuntimeError('cypher:'+str(d['errors'])[:400])
  return [x['row'] for x in d['results'][0]['data']]

 try:
  if not socket.gethostname().lower().startswith('deathstar') or out['querySha256']!='ef75210b5c841cebd272dd6973017c3d9695eec57831a3ec939d7f3c7975e9c3':raise ValueError('identity hold')
  mem={l.split(':')[0]:int(l.split()[1]) for l in open('/proc/meminfo') if l.startswith(('MemAvailable:','SwapFree:'))}
  if mem['MemAvailable']<12*1024**2 or mem['SwapFree']<2*1024**2:raise ValueError('resource hold')
  p=cmd(['docker','ps','-a','--format','{{.Names}}']);
  if p.returncode or NAME in p.stdout.splitlines():raise ValueError('container name hold')
  p=cmd(['docker','run','--pull','never','-d','--name',NAME,'--hostname','localhost','--network','none','--memory','2g','--memory-swap','2g','--cpus','2','--pids-limit','192','--security-opt','no-new-privileges','-e','NEO4J_AUTH=none','-e','NEO4J_server_memory_heap_initial__size=256m','-e','NEO4J_server_memory_heap_max__size=512m','-e','NEO4J_server_memory_pagecache_size=256m',IMAGE],20)
  if p.returncode:raise RuntimeError('create failed')
  cid=p.stdout.strip()
  if not re.fullmatch('[0-9a-f]{64}',cid):raise ValueError('container id')
  out['containerIdPrefix']=cid[:12];start=time.monotonic()
  while time.monotonic()-start<32:
   st=json.loads(cmd(['docker','inspect',cid,'--format','{{json .State}}']).stdout);log=cmd(['docker','logs','--tail','60',cid]);raw=log.stdout+log.stderr
   if not st['Running']:raise RuntimeError('startup exit '+str(st['ExitCode'])+' '+raw[-350:])
   if re.search(r'(?m)\bStarted\.$',raw):break
   time.sleep(1)
  else:raise RuntimeError('startup timeout')
  if c('RETURN 1 AS ready')!=[[1]]:raise ValueError('readiness')
  print('READY: isolated Neo4j 5.22',flush=True)
  for trial in range(3):
   task='task-race-'+str(trial);c("CREATE (:Task {id:'"+task+"',execution_attempt:1,claimed_by:'worker-a',claim_id:'claim-a',agent_session_id:'session-a',status:'RUNNING',lease_expires_at_ts:timestamp()+600000});")
   barrier=threading.Barrier(3)
   def writer(i):
    params={'task_id':task,'agent_id':'worker-a','completion_id':'same-key','claim_id':'claim-a','session_id':'session-a','status':'DONE','summary':'synthetic','result_json':'{}','run_id':f'run-{trial}-{i}'}
    barrier.wait(timeout=5);return c(query.strip(),params)
   with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(writer,range(3)))
   check=c("MATCH (t:Task {id:'"+task+"'}) OPTIONAL MATCH (t)-[:EXECUTED_BY]->(r:AgentRun) RETURN count(r) AS runs, t.status AS status;")
   print('trial',trial+1,'readback',check,flush=True);out['trials'].append({'trial':trial+1,'writers':3,'readback':check,'pass':check==[[1,'DONE']]})
  out['state']='native-race-pass' if all(x['pass'] for x in out['trials']) and len(out['trials'])==3 else 'native-race-failed'
 except Exception as e:out['errorClass']=type(e).__name__;out['errorDetail']=str(e)[:500]
 finally:
  if cid and re.fullmatch('[0-9a-f]{64}',cid):
   p=cmd(['docker','rm','-f','-v',cid],12);out['cleanupVerified']=p.returncode==0 and cid in p.stdout
 return out
if __name__=='__main__':
 print(json.dumps({'state':'plan-only','productionGraphUsed':False,'modelRequestsSent':0}))
