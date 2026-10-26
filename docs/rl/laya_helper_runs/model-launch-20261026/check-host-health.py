# Path: data/laya-orchestration/model-launch-20261026/supervision/check-host-health.py | Purpose: Snapshot transfer-aware model-cohort host health without changing active work.
import argparse,collections,datetime,fcntl,hashlib,json,pathlib,subprocess,time

root=pathlib.Path(__file__).resolve().parents[4]
task=root/'data/laya-orchestration/model-launch-20261026'
ap=argparse.ArgumentParser();ap.add_argument('output',type=pathlib.Path);args=ap.parse_args()
args.output.mkdir(parents=True,exist_ok=True)
plan=json.loads((task/'capacity15-20261027/transfer-plan.json').read_text())
registry=root/'data/laya-experiments';snap={};raw_snapshots={};parse_errors=[]
with (registry/'.host-admission.lock').open('r') as lock:
 fcntl.flock(lock,fcntl.LOCK_SH)
 for path in sorted(registry.glob('*/job-status.json')):
  try:
   data=path.read_bytes();d=json.loads(data)
   if isinstance(d,dict) and isinstance(d.get('jobs'),list):
    key=str(path.parent.resolve());snap[key]=(d,hashlib.sha256(data).hexdigest());raw_snapshots[key]=data
  except (OSError,ValueError) as e:parse_errors.append({'path':str(path),'error':str(e)})
 gate=json.loads((registry/'_host-scheduler-gate/job-status.json').read_text())
now=time.time();source=plan['source_registry'];owned={source}|{x['registry'] for x in plan['assignments']}
indexes={r:{j['id']:j for j in snap[r][0]['jobs']} for r in owned}
jobs=[dict(indexes[source][i],registry=source) for i in plan['untouched_started_jobs']]+[dict(indexes[x['registry']][x['id']],registry=x['registry']) for x in plan['assignments']]
assert len(jobs)==120 and len({j['id'] for j in jobs})==120
assert collections.Counter(j['arm'] for j in jobs)=={'guided':30,'vela':30,'jiwo':30,'kev':30}
assert all(j['seed'] in range(80300,80330) for j in jobs)
transfers=[indexes[source][x['id']] for x in plan['assignments']]
assert len(transfers)==113 and all(j['status']=='cancelled_queued' and not j.get('admission_reserved') and not any(j.get(k) for k in ['started','preparation_started','world','previous_attempts']) for j in transfers)
active=lambda j:bool(j.get('admission_reserved')) or j['status'] in ['running','preparing']
host_active=[dict(j,registry=r) for r,(d,_) in snap.items() if not pathlib.Path(r).name.startswith('_host-scheduler') for j in d['jobs'] if active(j)]
active_jobs=[j for j in jobs if active(j)]
units=[pathlib.Path(r).name+'.service' for r in sorted(owned)]+['laya-model-kev-r2-20261027.service','laya-model-vela-20261027.service','laya-model-jiwo-20261027.service','laya-model-serving-20261027.slice','laya-model-serving-20261027-worlds.slice','laya-host-scheduler.service']
unit_data={}
for unit in units:
 s=subprocess.check_output(['systemctl','--user','show',unit,'-p','LoadState','-p','ActiveState','-p','SubState','-p','MainPID','-p','InvocationID','-p','MemoryCurrent','-p','MemoryPeak','-p','MemoryMax','-p','MemorySwapMax','-p','ControlGroup','-p','Result','-p','ExecMainStatus','-p','RuntimeMaxUSec'],text=True)
 d=dict(x.split('=',1) for x in s.splitlines() if '=' in x)
 if d.get('ControlGroup'):
  cg=pathlib.Path('/sys/fs/cgroup'+d['ControlGroup'])
  for name in ['memory.events','memory.events.local','memory.pressure','cgroup.events','cgroup.procs']:
   if (cg/name).exists():d[name]=(cg/name).read_text()
 unit_data[unit]=d
def pid_info(pid):
 p=pathlib.Path('/proc')/str(pid)
 try:return {'pid':pid,'comm':(p/'comm').read_text().strip(),'cgroup':(p/'cgroup').read_text(),'start_ticks':(p/'stat').read_text().rsplit(')',1)[1].split()[19]}
 except (OSError,ValueError):return {'pid':pid,'present':False}
heartbeats=[];running=[]
for j in active_jobs:
 x={'id':j['id'],'arm':j['arm'],'status':j['status'],'registry':j['registry']}
 if j.get('world'):
  w=pathlib.Path(j['world']);x['world']=str(w)
  f=w/'laya-runner.heartbeat.json'
  if f.exists():
   x['heartbeat']=json.loads(f.read_text());x['heartbeat_age_seconds']=now-f.stat().st_mtime;heartbeats.append(x['heartbeat_age_seconds'])
  for name,pid in [('runner',j.get('runner_pid'))]:
   if pid:x[name]=pid_info(pid)
  f=w/'server/factorio.pid'
  if f.exists():
   try:x['factorio']=pid_info(int(f.read_text().strip()))
   except ValueError:x['factorio_pid_parse_error']=True
  f=w/'manifest.json'
  if f.exists():
   m=json.loads(f.read_text());x['ports']={k:m[k] for k in ['game_port','rcon_port']};x['runtime']=m.get('runtime');x['source_sha256']=m.get('source_sha256')
 running.append(x)
mem={k:int(v.strip().split()[0])*1024 for k,v in (l.split(':',1) for l in pathlib.Path('/proc/meminfo').read_text().splitlines())}
issues=[]
if len(host_active)>15:issues.append('host reservations exceed15 snapshot')
arm_active=dict(collections.Counter(j['arm'] for j in active_jobs))
if any(n>4 for n in arm_active.values()):issues.append('arm reservations exceed4 snapshot')
if now-gate['updated']>180:issues.append('host gate stale')
if parse_errors:issues.append('registry parse errors')
for unit,d in unit_data.items():
 for line in d.get('memory.events','').splitlines():
  k,v=line.split()
  if k in ['max','oom','oom_kill','oom_group_kill'] and int(v):issues.append(unit+':'+line)
ports=[p for j in running for p in j.get('ports',{}).values()]
if len(ports)!=len(set(ports)):issues.append('duplicate owned active ports')
out={'version':'laya-model-cohort-host-health-v1','checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'bounded host/unit/reservation/heartbeat only; decision journals verified separately','logical_planned':120,'logical_counts':dict(collections.Counter(j['status'] for j in jobs)),'logical_jobs':jobs,'logical_starts':sum(bool(j.get('started')) for j in jobs),'prior_attempts':sum(len(j.get('previous_attempts',[])) for j in jobs),'source_administrative_transfers':113,'active_by_arm':arm_active,'host_occupied':len(host_active),'other_reserved_jobs':[j for j in host_active if j['registry'] not in owned],'host_gate':dict(gate,age_seconds=now-gate['updated']),'registry_metadata':{r:{'updated':d.get('updated'),'age_seconds':now-d.get('updated',now),'sha256':sha,'admission':d.get('admission'),'admission_reason':d.get('admission_reason'),'host_budget':d.get('host_budget'),'counts':dict(collections.Counter(j['status'] for j in d['jobs']))} for r,(d,sha) in snap.items() if r in owned},'units':unit_data,'running_jobs':running,'max_heartbeat_age_seconds':max(heartbeats,default=None),'host_meminfo_bytes':mem,'host_memory_pressure':pathlib.Path('/proc/pressure/memory').read_text(),'parse_errors':parse_errors,'issues':issues}
# Preserve the exact bytes hashed under the admission lock, even if the live file changes later.
for r in owned:(args.output/(pathlib.Path(r).name+'-host-status.json')).write_bytes(raw_snapshots[r])
(args.output/'host-health.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'checked_utc':out['checked_utc'],'logical_counts':out['logical_counts'],'active_by_arm':arm_active,'host_occupied':len(host_active),'host_available_GiB':round(mem['MemAvailable']/1024**3,3),'max_heartbeat_age_seconds':out['max_heartbeat_age_seconds'],'host_gate_age_seconds':out['host_gate']['age_seconds'],'issues':issues}))
