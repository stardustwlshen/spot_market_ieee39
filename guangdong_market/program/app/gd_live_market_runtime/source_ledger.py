"""Incremental full-math source ledger. Hashes protect already-proved raw, never prove feasibility."""
from source import *
from source import _KEY
class LedgerSource(TypedSource):
 def __init__(self,host,*,windows=4):
  from hour_scope import plan
  self.windows=plan(windows)['windows']
  self._records=[];self.full_pair_checks=0;self._base_pins={};self._identity=None
  super().__init__(host)
  base=host.session.run;seed=base/'seed';warm=read(seed/'WARMSTART_SOURCE.json');da=Path(host.session.context['selected_DA'])
  paths=[*seed.glob('*.json'),base/'SHORT_CONTEXT.json',base/'INITIAL_CARRY.json',Path(warm['names']),Path(warm['response']),da/'ARTIFACTS.json']
  self._base_pins={str(f.resolve()):sha(f)for f in paths};self._identity=(host.branch_id,str(base),str(da),id(host.session),id(host.agc))
 def verify(self):
  super().verify()
  if self._identity is not None:
   h=self.host;e.need(self._identity==(h.branch_id,str(h.session.run),str(Path(h.session.context['selected_DA'])),id(h.session),id(h.agc)),'ledger live identity changed')
  for f,digest in self._base_pins.items():e.need(sha(f)==digest,'ledger seed/initial source changed')
 def accept(self,w):
  e.need(type(w)is int and 0<=w<self.host.window,'only registered accepted pair')
  self._ensure(w);return dict(window=w,registered=True,full_pair_checks=self.full_pair_checks,native_calls=0)
 def _ensure(self,w):
  self.verify();h=self.host;s=h.session;p=s.program
  # One synchronous validation: every record identity/digest remains checked.
  # Deduplicate only identical declared path/hash pairs; no cross-call cache.
  retained_pins={}
  for i,rec in enumerate(self._records):
   z=rec['data'];e.need(e.digest(z)==rec['digest'],'immutable source ledger payload changed')
   pp,aa=h.accepted_source(i);e.need(str(Path(pp))==z['prepared']and str(Path(aa))==z['actual'],'registered pair identity changed')
   e.need(str(Path(h.results[i]['handoff']['carry_path']))==z['out'],'registered carry identity changed')
   e.need(h.hooks.carries.get(z['out'])==z['pins'][z['out']],'carry registration changed')
   for f,digest in z['pins'].items():
    e.need(f not in retained_pins or retained_pins[f]==digest,'conflicting retained ledger source pin')
    retained_pins[f]=digest
  for f,digest in retained_pins.items():e.need(sha(f)==digest,'ledger source changed')
  while len(self._records)<=w:
   i=len(self._records);prep,actual=h.accepted_source(i);prep,actual=Path(prep),Path(actual)
   before=[*prep.glob('*.json'),actual/'ACCEPTED_PAIR.json',*[actual/st/f for st in ('SCUC','SCED')for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')]]
   pins={str(f.resolve()):sha(f)for f in before}
   n,ed=p.engine.checked_pair(prep,actual);q=read(actual/'SCUC/request.json.gz');u=read(actual/'SCUC/response.json.gz');eq=read(actual/'SCED/request.json.gz')
   pair_proof=e.verify_pair(q,u,eq,ed,n);e.dual_adapter().validate_record(actual,pair_proof);self.full_pair_checks+=1
   incoming=read(prep/'CARRY.json');previous=read(s.run/'INITIAL_CARRY.json')if not i else self._records[i-1]['data']['carry']
   for k in ('thermal','thermal_age_h','storage','notice_values','cutoff_tick','delivery_tick'):e.need(incoming[k]==previous[k],'live exact chain mismatch '+k)
   out=Path(h.results[i]['handoff']['carry_path']);e.need(h.hooks.carries.get(str(out))==sha(out),'unregistered exact handoff');carry=read(out)
   e.need(carry['executed_end_exclusive']==3*(i+1)and carry['issued_end_exclusive']==3*(i+2),'executed/issued clocks')
   pins[str(out.resolve())]=sha(out)
   for f,digest in carry.get('source_pins',{}).items():pins[f]=digest
   for f,digest in pins.items():e.need(sha(f)==digest,'source changed during full validation')
   report=dict(window=i,prepared=str(prep),actual=str(actual),SCED_response_sha256=read(actual/'SCED/COMPLETED.json')['response']['raw_sha256'],strict_pair_replayed=True,carry_registered=True,SCED_response_kind=pair_proof['SCED_response_kind'],dual_certificate_sha256=pair_proof['dual_certificate_sha256'],native_dual_passed=pair_proof['native_dual_passed'])
   shifted={};first=3*(i+1)
   for k,v in zip(n['variables'],ed['primal']):
    m=re.search(r'(\d+)\]$',k)
    if m:k=k[:m.start(1)]+str(first+int(m[1]))+']'
    shifted[k]=v
   delta={k:v for k,v in shifted.items()if(m:=re.search(r'(\d+)\]$',k))and int(m[1])<first+3}
   z=dict(prepared=str(prep),actual=str(actual),window_values=shifted,prefix_delta=delta,out=str(out),carry=carry,pins=pins,report=report)
   self._records.append(dict(data=copy.deepcopy(z),digest=e.digest(z)))
 def audit(self):
  self._ensure(len(self._records)-1)
  return dict(registered_windows=len(self._records),full_pair_checks=self.full_pair_checks,all_retained_sources_checked=True,math='each pair fully verified once before ledger insertion',native_calls=0)
 def snapshot(self,w):
  self.verify();h=self.host;s=h.session;p=s.program;base=s.run;ctx=s.context;seed=base/'seed';da=Path(ctx['selected_DA']);e.need(type(w)is int and -1<=w<self.windows,'source outside explicitly declared scope');e.need(w<0 or w<h.window,'missing actual accepted window')
  h.agc.da_plan();warm=read(seed/'WARMSTART_SOURCE.json');e.need(sha(warm['names'])==warm['names_sha256']and sha(warm['response'])==warm['response_sha256'],'seed raw source changed')
  req=read(seed/'request.json');origin=datetime.fromisoformat(req['origin']);values=dict(zip(read(warm['names'])['variables'],read(warm['response'])['primal']));prefix={k:v for k,v in values.items()if(m:=re.search(r'(\d+)\]$',k))and int(m[1])<3}
  pins=dict(s.registry['source_pins']);files=[*seed.glob('*.json'),base/'SHORT_CONTEXT.json',Path(warm['names']),Path(warm['response']),da/'ARTIFACTS.json'];reports=[];carry=read(base/'INITIAL_CARRY.json');files.append(base/'INITIAL_CARRY.json')
  retained_count=len(self._records)
  self._ensure(w)
  for i in range(w+1):
   # Recheck if _ensure inserted records through external pair validators.
   rec=self._records[i]
   if len(self._records)!=retained_count:e.need(e.digest(rec['data'])==rec['digest'],'immutable source ledger payload changed')
   z=rec['data'];prep,actual=Path(z['prepared']),Path(z['actual']);out=Path(z['out']);carry=z['carry']
   pins.update(z['pins']);prefix.update(z['prefix_delta']);reports.append(copy.deepcopy(z['report']))
  if w>=0:
   values=dict(prefix);values.update(self._records[w]['data']['window_values'])
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
