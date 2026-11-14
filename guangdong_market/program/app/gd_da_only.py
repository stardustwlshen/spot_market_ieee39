"""DA-only UI controller. No RT stage, no automatic retries."""
from pathlib import Path
import hashlib,json,subprocess,sys,os
B=Path(__file__).resolve().parent;CORE=B.parent/'engines/guangdong_v3/market.py'
def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2)
def plan(scenario,run):
 from gd_ui_scenarios import validate
 run=Path(run).resolve()
 if run.exists():raise ValueError('DA目录已存在；不重启/覆盖。读取已有状态。')
 if not scenario:raise ValueError('先另存并选择明确场景；DA-only不隐式回退模板')
 z=validate(CORE,scenario);root=Path(z['directory']);pins={str(root/f):h for f,h in z['files'].items()};pins[str(root/'SCENARIO.json')]=z['manifest_sha256']
 # Installed adapter and immutable engine code must not drift between plan and execution.
 for f in [B/'gd_da_only.py',B/'gd_da_only_worker.py',*CORE.parent.rglob('*.py')]:pins[str(f.resolve())]=sha(f)
 return dict(schema='gd.da.only.plan.v1',run=str(run),scenario=z,source_pins=pins,maximum_native_calls=3,default_native_calls=0,stages=['DA_SCUC','DA_SCED','independent_DA_PRICE','artifacts_review'],seconds=[3600,3600,3600],worker_wall_seconds=[3900,3900,3900],threads=1,mip_gap=.01,stage_mip_gap=[.01,None,None],gap_scope="MIP_only; LP_not_applicable",RT_calls=0,publication_allowed=False,formal_bill=None,automatic_retry=False)
def check(p):
 for f,h in p['source_pins'].items():
  if sha(f)!=h:raise ValueError('DA source drift: '+f)
def control(run):return Path(str(Path(run).resolve())+'_DA_ONLY')
def prepare(p):
 check(p);c=control(p['run']);c.mkdir();write(c/'PLAN.json',p)
 x=subprocess.run([sys.executable,'-B',str(B/'gd_da_only_worker.py'),'prepare',str(c)],capture_output=True,text=True);(c/'prepare.log').write_text(x.stdout+'\n'+x.stderr)
 if x.returncode:raise ValueError('DA纯准备失败；保留目录和日志，不重试')
 return status(p['run'])
def start(run,confirmed=False):
 if confirmed is not True:raise ValueError('explicit 3-call confirmation required')
 c=control(run);p=read(c/'PLAN.json');check(p)
 if not(c/'PREPARED.json').exists():raise ValueError('pure prepare required')
 write(c/'ATTEMPT.json',dict(maximum_native_calls=3,automatic_retry=False))
 with(c/'native.log').open('xb')as f:q=subprocess.Popen([sys.executable,'-B',str(B/'gd_da_only_worker.py'),'run',str(c),'--execute-native'],stdout=f,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 write(c/'PID.json',dict(pid=q.pid));return dict(status='started',run=str(run))
def status(run):
 c=control(run);p=read(c/'PLAN.json');check(p);r=Path(p['run']);a=read(c/'ACCEPTED.json')if(c/'ACCEPTED.json').exists()else None
 if a and sha(r/'ARTIFACTS.json')!=a['ARTIFACTS_sha256']:raise ValueError('accepted DA artifact drift')
 return dict(run=str(r),prepared=(c/'PREPARED.json').exists(),attempted=(c/'ATTEMPT.json').exists(),exit=read(c/'EXIT.json')if(c/'EXIT.json').exists()else None,accepted=a,publication_allowed=False,RT_calls=0)
