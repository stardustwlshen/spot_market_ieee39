"""Bounded 4/96-window research clock plan. No native entry point or authorization."""
from pathlib import Path
from datetime import timedelta
import sys,copy,json
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_unified_event_agc_host_v1_20261113'))
from agc_lifecycle import AgcLifecycle,read,sha,need,stamp,digest,produce,write

def schedule(windows=4):
 need(type(windows)is int and windows in (4,96),'windows4 or96 only')
 return dict(windows=windows,default_windows=4,default_native_calls=0,new_full_day_validated=False,capability_blockers=['nextday synthetic AGC parameter validity declaration required','explicit tiny-boundary-normalization receipt adapter absent; no silent clipping'],events=[dict(hour=h,source='same selected DA'if h==0 else'accepted RT',source_window=None if h==0 else 4*h-3,available_cutoff_tick=-6 if h==0 else 12*h-6,first_consumer_window=0 if h==0 else 4*h-2)for h in range(25)if h==0 or 4*h-2<windows],executed_end_tick=3*windows,issued_end_tick=3*windows+3,H25_authorized=False)
def due_hours(window):
 need(type(window)is int and 0<=window<96,'window0..95')
 return [h for h in range(25)if h==0 or 12*h-6<=3*window]
def declaration_check(carry,origin,seed_sha,da_response_sha,cutoff):
 path=Path(carry['day_declaration_path']);need(sha(path)==carry['day_declaration_sha256'],'declaration drift')
 d=read(path);need(d['mode']=='hypothetical_published_research','explicit research declaration required');date=(origin+timedelta(days=1)).date().isoformat();need(date in d['days'],'nextday declaration absent');x=d['days'][date]
 need(stamp(x['published_at'])<=stamp(x['available_at'])<=stamp(cutoff),'nextday declaration unavailable')
 need(x['source']['case_sha256']==seed_sha and x['source']['accepted_original_DA_response_sha256']==da_response_sha,'nextday DA/seed source mismatch')
 need(x['artifact_sha256']==digest(x['artifact']),'nextday artifact changed')
 return dict(date=date,path=str(path),sha256=sha(path),available_at=x['available_at'],basis=x['basis'],actual_publication_verified=False)

class DayAgcLifecycle(AgcLifecycle):
 def create(self,hour):
  if hour<24:return super().create(hour)
  need(hour==24,'H25 not computed/authorized');self.verify();host=self.clock_host;host.session.verify_sources();cp=Path(host.cp);need(str(cp)in host.hooks.carries and sha(cp)==host.hooks.carries[str(cp)],'live carry drift');carry=read(cp)
  need(stamp(carry['cutoff'])>=self.origin+timedelta(hours=23,minutes=30),'H24 not yet due')
  # Reauthenticate selected DA via the existing fixed-source adapter, then D+1.
  _,_,da=self.da_plan();proof=declaration_check(carry,self.origin,self.seed_sha,da['SCED_response_sha256'],carry['cutoff'])
  case,v,plan=self.rt_plan(24);cells=plan['cells'];need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400 unchanged')
  plan.update(schema='gd.agc.verified_plan.v1',branch_id=self.context['run_id'],seed_case_sha256=self.seed_sha,zone='explicit_single_research_zone',availability_basis='explicit hypothetical nextday declaration; not telemetry',nextday_declaration=proof,thermal={g:dict(stable=[v[f'stable[{g},{t}]']for t in range(cells)],power=[v[f'power[{g},{t}]']for t in range(cells)],minimum=[d['min_mw'][t]for t in range(cells)],maximum=[d['max_mw'][t]for t in range(cells)])for g,d in case['energy_inputs']['thermal'].items()},formal=False,safety_release=False)
  plan['source_pins'][proof['path']]=proof['sha256']
  import agc_lifecycle as original
  base=read(original.B/'agc_runtime/RESEARCH_INPUT.json');path=getattr(self,'nextday_agc_path',None)
  need(path is not None,'explicit host-pinned nextday AGC validity declaration required; original expires at midnight')
  path=Path(path).resolve();need(str(path)in host.session.registry['source_pins'] and sha(path)==host.session.registry['source_pins'][str(path)],'unregistered nextday AGC declaration')
  from nextday_declaration import validate
  data=validate(path,cutoff=carry['cutoff']);need(data['units']==base['units'],'qualification/inventory/bids changed')
  need(set(data['parameters'])==set(base['parameters']),'parameter inventory changed')
  for k,x in base['parameters'].items():
   need({f:v for f,v in data['parameters'][k].items()if f not in ('effective_from','effective_to')}=={f:v for f,v in x.items()if f not in ('effective_from','effective_to')},'nextday numerical/source parameter changed')
  plan['source_pins'][str(path)]=sha(path);start=self.origin+timedelta(days=1);data.update(hour_start=start.isoformat(),as_of=(start-timedelta(minutes=30)).isoformat());r=produce(plan,data,expected_plan_sha256=digest(plan),branch_id=self.context['run_id'],seed_case_sha256=self.seed_sha);need(r['result']['awards_mw']=={g:10 for g in plan['thermal']},'changed synthetic awards unsupported');return r
 def produce(self,hour,*,cutoff):
  if hour<24:return super().produce(hour,cutoff=cutoff)
  need(hour==24 and stamp(cutoff)>=self.origin+timedelta(hours=23,minutes=30),'H24notdue/H25unsupported');self.verify();f=self.root/'H24.json'
  if hour in self.issued:
   r=self.issued[hour];need(f.exists()and read(f)==r,'receipt drift')
   for p,h in r['verified_plan']['source_pins'].items():need(sha(p)==h,'source drift')
   self.accepted_source(93)
  else:
   need(not f.exists(),'persisted untrusted H24');r=self.create(24);write(f,r);self.issued[24]=copy.deepcopy(r)
  return copy.deepcopy(r)
 def apply_before_scuc(self,kw,carry):
  kw,binding=super().apply_before_scuc(kw,carry)
  cut=stamp(carry['cutoff']);begin=stamp(kw['contract']['domain_start']);target=self.origin+timedelta(days=1)
  if cut<target-timedelta(minutes=30)or not(begin<target+timedelta(hours=1)and begin+timedelta(hours=4)>target):return kw,binding
  from batch_api import apply_available
  r=self.produce(24,cutoff=cut.isoformat());thermal={g:dict(maximum=[d['max_mw'][t]for t in range(48)],minimum=[d['min_mw'][t]for t in range(48)])for g,d in kw['energy_inputs']['thermal'].items()};consumer=dict(branch_id=self.context['run_id'],seed_case_sha256=self.seed_sha,cutoff=cut.isoformat(),delivery=begin.isoformat(),domain_start=begin.isoformat(),cells=48,thermal=thermal,zone='explicit_single_research_zone',positive_reserve_mw=[kw['reserve']['positive_mw'][t]for t in range(48)],accident_reserve_mw=[kw['reserve']['contingency_mw'][t]for t in range(48)])
  base=json.loads(json.dumps(kw['agc_awards']));z=apply_available([r],consumer,base,expected_receipt_digests=[digest(r)],expected_consumer_digest=digest(consumer));same=z['agc']==base
  kw['agc_awards'].clear();kw['agc_awards'].update({g:{**a,**{side:{int(t):v for t,v in a[side].items()}for side in ('up_mw','down_mw')}}for g,a in z['agc'].items()})
  binding.update(same_original_awards=same,requires_new_SCUC_SCED_and_safety=True,old_raw_compatibility_not_assumed=True)
  binding['receipt_files'][str(self.root/'H24.json')]=sha(self.root/'H24.json');binding['applied']+=z['applied_receipts'];binding['issuer_receipt_sha256'].append(digest(r));binding['H24_nextday_declaration']=r['verified_plan']['nextday_declaration'];return kw,binding
class DayClockAdapter:
 def __init__(self,host,*,windows=4,nextday_agc_path=None):
  from unified import RollingHost
  need(isinstance(host,RollingHost),'live registered host required');schedule(windows);need(host.pending is None,'cannot alter prepared occurrence');self.host=host;self.windows=windows
  need(type(host.agc)is AgcLifecycle,'only original lifecycle supported');host.agc.__class__=DayAgcLifecycle;host.agc.clock_host=host;host.agc.nextday_agc_path=nextday_agc_path;host.session.registry['source_pins'][str(Path(__file__).resolve())]=sha(__file__)
 def prepare(self):
  h=self.host;need(h.pending is None and h.window<self.windows,'window bound/pending');h.session.verify_sources();cp=Path(h.cp);need(str(cp)in h.hooks.carries and sha(cp)==h.hooks.carries[str(cp)],'live carry drift');need(read(cp)['cutoff_tick']==3*h.window,'window clock mismatch')
  import thermal_physical
  old=thermal_physical.build_free_scuc;bindings=[]
  def hook(**kw):
   _,binding=h.agc.apply_before_scuc(kw,read(cp));bindings.append(binding);return old(**kw)
  thermal_physical.build_free_scuc=hook;p=h.output/f'W{h.window}'/'prepared';p.parent.mkdir(exist_ok=False)
  try:result=h.hooks.build(cp,p,ledger_id=h.lid)
  finally:thermal_physical.build_free_scuc=old
  need(len(bindings)==1,'single AGC binding');write(p.parent/'AGC_BINDING.json',bindings);h.pending=p;h.checkpoint('prepared');return result
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--windows',type=int,default=4);a=p.parse_args();print(json.dumps(schedule(a.windows),indent=2))
