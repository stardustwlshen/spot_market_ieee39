"""Installable short-run research PRICE user entry. Native requires explicit flag."""
from loader import *
import argparse

def prepare_price(da,run,w,parameters,output):
 from parameters import resolved
 from domain_v23 import prepare
 from wiring import apply_slacks
 from mapping import build
 from source_contracts import selected_da
 da=Path(da).resolve();run=Path(run).resolve();out=Path(output).resolve();need(not out.exists(),'fresh output only');selected_da(da);verified,proof,case=inputs(da,run,w);a,r,x=verified;z,c,pars,boundary=resolved(parameters,case,da,run,w,r)
 for g,states in z['eligibility'].items():
  for q,status in enumerate(states):
   ts=range(r['delivery_tick']+3*q,r['delivery_tick']+3*q+3)
   need(all(x[f'off[{g},{t}]']>.5 and x[f'online[{g},{t}]']<.5 for t in ts) if status=='off' else all(x[f'stable[{g},{t}]']>.5 and x[f'online[{g},{t}]']>.5 for t in ts),'explicit eligibility conflicts with accepted state')
 out.mkdir(parents=True);pair=run/f'W{w}/actual/ACCEPTED_PAIR.json';prepare(pair,run/'seed/case.json',run/'seed/agc.json',da,out/'domain',research_current_sced=True,boundary=boundary,verified_inputs=verified,explicit=z)
 q=read(out/'domain/template.json');n=read(out/'domain/names.json');ctx=read(out/'domain/CONTEXT.json');q,n,trace=apply_slacks(q,n,build(q,n),z,pars,r['cutoff']);ctx.update(short_run=str(run),selected_DA=str(da),physical_release=False,safety_release=False,formal_bill=None,price_producer='gd.short.PRICE.user.v1')
 for name,value in [('template.json',q),('names.json',n),('CONTEXT.json',ctx),('CONTRACT.json',c),('SOURCE_REVIEW.json',proof),('TRACE.json',trace)]:write(out/name,value)
 files=[*B.glob('*.py'),B/'TRUSTED_RT.json',*LEG.glob('*.py'),Path(parameters).resolve(),pair,da/'ARTIFACTS.json'];pins={str(p):sha(p)for p in files};pins.update(proof['source_pins']);write(out/'READY.json',dict(columns=len(q['variables']),rows=len(q['rows']),artifact_pins={p.name:sha(p)for p in out.iterdir()if p.is_file()},source_pins=pins,native_calls=0,formal=False))
 return dict(status='prepared',prepared=str(out),native_calls=0,columns=len(q['variables']),rows=len(q['rows']),start=ctx['start'],end=ctx['end'],domain=boundary)

def native_price(prepared,execute):
 from native import run
 p=Path(prepared).resolve();ready=read(p/'READY.json');verify({str(p/f):h for f,h in ready['artifact_pins'].items()});verify(ready['source_pins'])
 if (p/'native_once').exists():
  need((p/'native_once/COMPLETED.json').exists(),'unknown native occurrence; never retry');read_native(p);return dict(status='completed',native_calls=0,completed_existing=True)
 if not execute:return dict(status='ready',native_calls=0,requires_execute_native=True,columns=ready['columns'])
 z=run(p,True);return dict(status='completed',**z)
def read_native(p):
 from evidence import optimal
 from scoped_kkt import metrics
 d=p/'native_once';r=read(d/'response.json');q=read(d/'request.json');n=read(p/'names.json');need(read(d/'COMPLETED.json')['response_sha256']==sha(d/'response.json'),'completed response');need(read(d/'IDENTITY.json')['request_sha256']==sha(d/'request.json'),'native request');need({k:v for k,v in q.items()if k!='deadline'}==read(p/'template.json'),'selected matrix');optimal(q,r,n);return r,n

def accept_price(prepared,output):
 from review import review
 z=review(Path(prepared),Path(output));return dict(status='accepted',**z)
def read_result(path):
 p=Path(path).resolve();z=read(p);need(z.get('schema')in ['gd.independent_PRICE.accepted.v2','gd.short.first_hour.counterfactual.Decimal.v26','gd.short.PRICE.hour.v1','gd.PRICE.reused_mathematical_witness.v26'],'supported research result');verify(z['source_pins'])
 if z.get('schema')=='gd.independent_PRICE.accepted.v2':
  from scoped_kkt import metrics
  files=list(z['source_pins']);rp=[Path(f)for f in files if Path(f).name=='response.json'and Path(f).parent.name=='native_once'];qp=[Path(f)for f in files if Path(f).name=='request.json'and Path(f).parent.name=='native_once'];np=[Path(f)for f in files if Path(f).name=='names.json'];need(len(rp)==len(qp)==len(np)==1,'unique raw PRICE identity');raw=read(rp[0]);names=read(np[0]);kkt=metrics(read(qp[0]),raw,names,angle_tolerance=1e-8);need(kkt['passed'],'raw fullKKT read');prices={s.split('[',1)[1].split(',')[0].strip("'"):v/.25 for s,v in zip(names['constraints'],raw['dual'])if s.startswith('balance[')and s.endswith(',0]')};need(prices==z['raw_prices'],'edited prices differ from raw PRICE duals')
 if 'raw_prices'in z:need(z['KKT']['passed'] and len(z['raw_prices'])==39,'complete prices');return dict(status='accepted',native_calls=0,read_only=True,source=str(p),context=z['context'],raw_prices=z['raw_prices'],KKT=z['KKT'])
 return dict(status='accepted',native_calls=0,read_only=True,source=str(p),rows=len(z['rows']),executed_cells=z['executed_cells'],hour_prices=z['hour_prices'],total_energy_component_CNY=z['total_energy_component_CNY'])

def execution_view(da,run,hour,output):
 need(type(hour)is int and 0<=hour<24,'hour0..23');da,run,c,r=context(da,run);w=4*(hour+1)-1;verified,proof,case=inputs(da,run,w)
 p=subprocess.run([sys.executable,str(B/'trusted_rt_bridge.py'),str(run),str(w+1),'carry'],capture_output=True,text=True);need(p.returncode==0,'complete target handoff absent or invalid: '+p.stderr[-300:]);carry=json.loads(p.stdout.strip().splitlines()[-1])['carry'];need(carry['executed_end_exclusive']>=12*(hour+1),'issued not executed');start=12*hour;end=12*(hour+1);x={k:v for k,v in verified[2].items()if(m:=re.search(r'(\d+)\]$',k))and start<=int(m[1])<end}
 files=[run/'SHORT_CONTEXT.json',run/f'handoff/W{w+1}/HANDOFF.json',run/f'handoff/W{w+1}/CARRY.json',run/'seed/request.json',run/'seed/case.json',da/'ARTIFACTS.json',Path(__file__),B/'loader.py',B/'trusted_rt_bridge.py'];pins={str(p):sha(p)for p in files};pins.update(proof['source_pins']);view=dict(schema='gd.short.PRICE.execution_view.v1',run_id=c['run_id'],run=str(run),selected_DA=str(da),hour=hour,origin=r['origin'],executed_end=carry['executed_end_exclusive'],issued_end=carry['issued_end_exclusive'],cells=list(range(start,end)),values=x,source_pins=pins,formal_bill=None,safety_release=False);write(output,view);return dict(status='prepared',native_calls=0,execution=str(output),executed_cells=12,hour=hour)

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['parameters','prepare','native','accept','read','execution-view','hour']);p.add_argument('--da');p.add_argument('--run');p.add_argument('--window',type=int,default=0);p.add_argument('--output');p.add_argument('--name');p.add_argument('--domain',choices=['current_sced_research','DA_reference_R61_compatible_research'],default='current_sced_research');p.add_argument('--parameters');p.add_argument('--prepared');p.add_argument('--execute-native',action='store_true');p.add_argument('--accepted');p.add_argument('--hour',type=int,default=0);p.add_argument('--execution');p.add_argument('--quarters',nargs=4);a=p.parse_args()
 try:
  if a.action=='parameters':
   from parameters import sample
   z=sample(a.da,a.run,a.window,a.output,a.name,a.domain)
  elif a.action=='prepare':z=prepare_price(a.da,a.run,a.window,a.parameters,a.output)
  elif a.action=='native':z=native_price(a.prepared,a.execute_native)
  elif a.action=='accept':z=accept_price(a.prepared,a.output)
  elif a.action=='read':z=read_result(a.accepted)
  elif a.action=='execution-view':z=execution_view(a.da,a.run,a.hour,a.output)
  else:
   from hour import compute
   z=compute(a.da,a.execution,a.quarters,a.output)
  z.update(formal=False,safety_release=False,payable=False);print(json.dumps(z))
 except Exception as e:print(json.dumps(dict(status='blocked',error=str(e),native_calls='unknown_check_receipt_no_retry'if a.action=='native'and a.execute_native else 0,formal=False,safety_release=False,payable=False)));raise SystemExit(1)
if __name__=='__main__':main()
