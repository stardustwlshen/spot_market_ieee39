"""In-memory authority from the registered live RollingHost, never receipt-selected code."""
from pathlib import Path
import sys,json,hashlib,re,copy
from datetime import datetime,timedelta
B=Path(__file__).resolve().parent;APP=B.parent;RT=APP/'lifecycle_runtime'
sys.path[:0]=[str(RT),str(RT/'guangdong_unified_event_agc_host_v1_20261113')]
from portable_gate import verify as portable_verify
from unified import RollingHost
from v6_host import sha,read,e
_KEY=object()
class Snapshot:
 def __init__(self,key,owner,data):
  e.need(key is _KEY,'only live source may create snapshot');self.owner=owner;self.data=data;self.pins=copy.deepcopy(data['source_pins']);self.digest=e.digest(data)
 def verify(self):
  self.owner.verify();e.need(e.digest(self.data)==self.digest,'snapshot payload changed')
  for f,h in self.pins.items():e.need(sha(f)==h,'live snapshot source changed')
  e.need(self.data['branch_id']==self.owner.host.branch_id,'host identity changed')
  return copy.deepcopy(self.data)
class TypedSource:
 def __init__(self,host):
  portable_verify();e.need(isinstance(host,RollingHost),'registered live RollingHost required, not JSON');e.need(host.hooks.session is host.session,'host/session mismatch');self.host=host;self.verify()
 def verify(self):
  portable_verify();self.host.session.verify_sources();self.host.agc.verify();e.need(self.host.session.run==self.host.agc.run,'AGC/run mismatch')
  cp=Path(self.host.cp);e.need(self.host.hooks.carries.get(str(cp))==sha(cp),'unregistered current exact carry')
 def snapshot(self,w):
  self.verify();h=self.host;s=h.session;p=s.program;base=s.run;ctx=s.context;seed=base/'seed';da=Path(ctx['selected_DA']);e.need(type(w)is int and -1<=w<=3,'bounded H0 source');e.need(w<0 or w<h.window,'missing actual accepted window')
  h.agc.da_plan();warm=read(seed/'WARMSTART_SOURCE.json');e.need(sha(warm['names'])==warm['names_sha256']and sha(warm['response'])==warm['response_sha256'],'seed raw source changed')
  req=read(seed/'request.json');origin=datetime.fromisoformat(req['origin']);values=dict(zip(read(warm['names'])['variables'],read(warm['response'])['primal']));prefix={k:v for k,v in values.items()if(m:=re.search(r'(\d+)\]$',k))and int(m[1])<3}
  pins=dict(s.registry['source_pins']);files=[*seed.glob('*.json'),base/'SHORT_CONTEXT.json',Path(warm['names']),Path(warm['response']),da/'ARTIFACTS.json'];reports=[];carry=read(base/'INITIAL_CARRY.json');files.append(base/'INITIAL_CARRY.json')
  for i in range(w+1):
   prep,actual=h.accepted_source(i);prep,actual=Path(prep),Path(actual);n,ed=p.engine.checked_pair(prep,actual);q=read(actual/'SCUC/request.json.gz');u=read(actual/'SCUC/response.json.gz');eq=read(actual/'SCED/request.json.gz');pair_proof=e.verify_pair(q,u,eq,ed,n);e.dual_adapter().validate_record(actual,pair_proof)
   incoming=read(prep/'CARRY.json')
   for k in ('thermal','thermal_age_h','storage','notice_values','cutoff_tick','delivery_tick'):e.need(incoming[k]==carry[k],'live exact chain mismatch '+k)
   out=Path(h.results[i]['handoff']['carry_path']);e.need(h.hooks.carries.get(str(out))==sha(out),'unregistered exact handoff');carry=read(out)
   for f,digest in carry.get('source_pins',{}).items():e.need(sha(f)==digest,'registered carry dependency changed');pins[f]=digest
   e.need(carry['executed_end_exclusive']==3*(i+1)and carry['issued_end_exclusive']==3*(i+2),'executed/issued clocks')
   values=dict(prefix);first=3*(i+1)
   for k,v in zip(n['variables'],ed['primal']):
    m=re.search(r'(\d+)\]$',k)
    if m:k=k[:m.start(1)]+str(first+int(m[1]))+']'
    values[k]=v
   prefix={k:v for k,v in values.items()if(m:=re.search(r'(\d+)\]$',k))and int(m[1])<first+3}
   files += [*prep.glob('*.json'),out,actual/'ACCEPTED_PAIR.json',*[actual/st/f for st in ('SCUC','SCED')for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')]]
   reports.append(dict(window=i,prepared=str(prep),actual=str(actual),SCED_response_sha256=read(actual/'SCED/COMPLETED.json')['response']['raw_sha256'],strict_pair_replayed=True,SCED_response_kind=pair_proof['SCED_response_kind'],dual_certificate_sha256=pair_proof['dual_certificate_sha256'],native_dual_passed=pair_proof['native_dual_passed'],carry_registered=True))
  for k,v in list(values.items()):
   if k.startswith('battery.charge['):values['battery.power'+k[len('battery.charge'):]]=values['battery.discharge'+k[len('battery.charge'):]]-v
  request=copy.deepcopy(req);request.update(run_id=h.branch_id,window=w,delivery_tick=3*(w+1),cutoff=(origin+timedelta(minutes=15*w)).isoformat());agc=read(seed/'agc.json')
  if w>=0:
   prep,actual=h.accepted_source(w);local=read(prep/'agc.json');from gd_post_scuc_runtime import consumers;local=consumers.effective_awards(prep,actual,local);first=request['delivery_tick']
   for g in agc:
    for side in ('up_mw','down_mw'):
     for t,v in local[g][side].items():agc[g][side][str(first+int(t))]=v
   d=p.engine.loads((prep/'derived_case.json').read_bytes());request['declared_terminal_by_resource']={g:{k:z[k]for k in ('terminal_at','terminal_energy_mwh')}for g,z in d['batteries'].items()};response=reports[-1]['SCED_response_sha256']
  else:response=warm['response_sha256']
  for f in files:pins[str(f.resolve())]=sha(f)
  for f in B.glob('*.py'):pins[str(f)]=sha(f)
  data=dict(schema='gd.live_host.PRICE_snapshot.v3',branch_id=h.branch_id,branch=str(getattr(h,'source_branch',h.output)),base_run=str(base),selected_DA=str(da),source_kind=getattr(h,'source_kind','live_native_host'),window=w,request=request,accepted=dict(run_id=h.branch_id,window=w,SCED_response_sha256=response),values=values,effective_agc=agc,reports=reports,executed_end=0 if w<0 else carry['executed_end_exclusive'],issued_end=3 if w<0 else carry['issued_end_exclusive'],source_pins=pins,formal=False,safety_release=False,native_calls=0)
  if w>=0:
   from cycle_boundary_contract import produce
   request['rolling_cycle_boundary']=produce(request,data['accepted'],reports[-1])
   pins.update(request['rolling_cycle_boundary']['pins'])
   from gd_post_scuc_runtime import consumers
   data['AGC_forecast_semantics']=consumers.semantics_for(prep,actual)
  return Snapshot(_KEY,self,data)
