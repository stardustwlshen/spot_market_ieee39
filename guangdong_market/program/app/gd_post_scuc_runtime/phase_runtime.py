"""Pure post-SCUC allocation/rebuild phase. Not installed runtime or a native runner."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib,json,sys,math
B=Path(__file__).resolve().parent;sys.path.insert(0,str(B/'vendor'))
from adapter import run as clear

def need(x,msg):
 if not x:raise ValueError(msg)
def packed(x):
 from dataclasses import is_dataclass,asdict
 if isinstance(x,bytes):return {'bytes_hex':x.hex()}
 if is_dataclass(x):return packed(asdict(x))
 if isinstance(x,dict):return {'map':sorted([[packed(k),packed(v)]for k,v in x.items()],key=lambda z:repr(z[0]))}
 if isinstance(x,(tuple,list)):return [packed(v)for v in x]
 return x
def digest(x):return hashlib.sha256(json.dumps(packed(x),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
_NATIVE=object()
def stamp(x):
 d=datetime.fromisoformat(x);need(d.utcoffset()==timedelta(hours=8),'explicit +08');return d

def fixed(q,values):
 r=deepcopy(q)
 for v,x in zip(r['variables'],values):
  if v['kind']!='C':need(abs(x-round(x))<=1e-6,'integer witness');v.update(kind='C',lb=float(round(x)),ub=float(round(x)),start=None)
 return r

class PostSCUC:
 def __init__(self,*,builder,case,source_model,names,primal,audit,branch_id,source_id,domain_start,internal_completed_at,mode,_native_token=None):
  need(mode in ('native_internal_stage','explicit_analytic_fixture'),'source kind; no oldraw compatible mode')
  need(mode!='native_internal_stage' or _native_token is _NATIVE,'native stage must enter from_native; JSON is not source authority')
  self.builder=builder;self.case=deepcopy(case);self.q=deepcopy(source_model);self.n=deepcopy(names);self.x=list(primal);self.audit=audit;self.branch=branch_id;self.source=source_id;self.begin=stamp(domain_start);self.completed=stamp(internal_completed_at);self.mode=mode
  need(branch_id and source_id,'source identity')
  rebuilt,nn=builder(self.case);need(rebuilt==self.q and nn==self.n,'source does not equal complete trusted builder result')
  a=audit(self.q,self.x,None);need(a['max_primal_violation']<=1e-6 and a['max_integer_violation']<=1e-6,'SCUC full physical/integer witness rejected')
  need(all(v==400 for k in ('positive_mw','contingency_mw')for v in case['reserve'][k].values()),'400/400 unchanged')
  self.binding=digest(dict(model=self.q,names=self.n,primal=self.x,branch=branch_id,source=source_id,case=self.case))
 @classmethod
 def from_native(cls,*,program,prepared,case,builder,branch_id,domain_start,internal_completed_at):
  p=Path(prepared);q=json.loads((p/'template.json').read_text());n=json.loads((p/'names.json').read_text());stage=program.stage(p.parent,'SCUC',q,n,True)
  need(stage is not None,'completed registered SCUC required');actual,r=stage
  need(r['status']=='optimal'and r['native_status']=='Optimal'and r['native_mip_gap']<=actual['options']['mip_rel_gap']+1e-12,'actual accepted SCUC required')
  # Full builder comparison is independent of original worker options/deadline.
  built,nn=builder(case)
  for key in ('variables','rows','objective'):need(built[key]==actual[key],'SCUC builder mismatch '+key)
  return cls(builder=builder,case=case,source_model=built,names=nn,primal=r['primal'],audit=_native_audit,branch_id=branch_id,source_id=hashlib.sha256((p.parent/'actual/SCUC/response.json.gz').read_bytes()).hexdigest(),domain_start=domain_start,internal_completed_at=internal_completed_at,mode='native_internal_stage',_native_token=_NATIVE)
 def prepare(self,data,*,expected_source,stage_clock,price_policy):
  need(expected_source==self.binding,'wrong SCUC source');need(price_policy=='DA_band_intersect_current_AGC_research','explicit research PRICE policy required')
  now=stamp(stage_clock);cut=stamp(data['as_of']);start=stamp(data['hour_start']);end=start+timedelta(hours=1)
  need(self.completed<=cut==start-timedelta(minutes=30)<=now,'SCUC/allocator internal order or T-30 publication deadline')
  need(now==cut,'unannounced future or backdated receipt consumption refused')
  need(self.begin==stamp(self.case['contract'].get('domain_start',self.case['contract'].get('day_start'))),'source physical clock mismatch')
  need(cut<=stamp(self.case['contract']['cutoff']),'receipt later than builder causal cutoff; do not backdate awards')
  n=len(self.case['energy_inputs']['horizon'].ids);ticks=[t for t in range(n)if start<=self.begin+timedelta(minutes=5*t)<end];need(len(ticks)==12,'whole target hour unavailable')
  values=dict(zip(self.n['variables'],self.x));thermal=self.case['energy_inputs']['thermal']
  from .participation import allocate
  result,participation=allocate(data,self.case,values,ticks,clear)
  awards=result['awards_mw']
  c=deepcopy(self.case)
  for g,a in c['agc_awards'].items():
   a['available_at']=cut.isoformat();a['source']='postSCUC_internal_research:'+digest(data)
   for t in ticks:
    for side in ('up_mw','down_mw'):a[side][t]=awards.get(g,0)
  rebuilt,names=self.builder(c);need(names['variables']==self.n['variables'],'variable identity changed');need(rebuilt['objective']==self.q['objective'],'objective changed');need(rebuilt['variables']==self.q['variables'],'underlying column physics changed')
  lp=fixed(rebuilt,self.x)
  # Stable locks are tested against original integer choices; no implicit recommit.
  for g,v in awards.items():
   if v>0:need(all(values[f'stable[{g},{t}]']>.5 for t in ticks),'incompatible integer commitment')
  receipt=dict(schema='gd.agc.post_scuc.research.v1',source_kind=self.mode,SCUC_source=self.source,SCUC_binding=self.binding,branch_id=self.branch,internal_completed_at=self.completed.isoformat(),published_at=cut.isoformat(),hour_start=start.isoformat(),hour_end=end.isoformat(),allocator=result,SCED_not_yet_solved=True,formal=False)
  proof=dict(schema='gd.post_scuc_agc.phase_proof.v1',source=self.binding,receipt=digest(receipt),case=digest(c),model=digest(lp),names=digest(names),integer_source=digest(self.x),price_policy=price_policy,same_awards=all(c['agc_awards'][g][side]==self.case['agc_awards'][g][side]for g in c['agc_awards']for side in ('up_mw','down_mw')),native_calls=0,boundary_unchanged=True,requires_new_SCED=True)
  return dict(model=lp,names=names,case=c,receipt=receipt,proof=proof,allocation_input=deepcopy(data),stage_clock=stage_clock,participation=participation)
 def verify(self,candidate):
  fresh=self.prepare(candidate['allocation_input'],expected_source=self.binding,stage_clock=candidate['stage_clock'],price_policy=candidate['proof']['price_policy']);need(candidate==fresh,'phase differs from deterministic allocator/full builder reconstruction');return fresh['proof']

def _native_audit(q,x,unused):
 # Existing original audit, never an alternate tolerance or invented acceptance.
 from smm.gd_r23_equivalent_compression import audit
 objective=q['objective']['constant']+math.fsum(c*x[j]for j,c in q['objective']['terms'])
 return audit(q,x,objective)
