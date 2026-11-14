"""Explicit, source-bound research simulation command. Never dispatch authority."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
import sys,hashlib,json
B=Path(__file__).resolve().parent;R=B.parent
sys.path[:0]=[str(R/'guangdong_general_event_runner_v1_20261113'),str(R/'guangdong_shutdown_instruction_candidate_v1_20261113')]
from runner import GeneralRollingHost
from instructions import ResearchInstructions,e,read,sha

def apply_records(model,records):
 """Internal data kernel; callers MUST authenticate records, no JSON loader."""
 c=model._r23['inputs'];cut=e.tick(c['contract']['cutoff'],c['contract']['origin']);start=e.tick(c['contract']['domain_start'],c['contract']['origin']);trace=[]
 for r in records:
  g=r['unit'];at=r['begin_tick'];local=at-start
  if not 0<=local<len(model.T):continue
  e.need(r['research_only']is True and r['official']is False and r['as_of_tick']<=cut,'unavailable/nonresearch stop command')
  e.need(r['rows']==c['curves'][g]['shutdown'],'declared shutdown curve changed')
  # Keep AGC constraints; fail early rather than quietly making the mandate infeasible.
  for t in range(local,min(local+len(r['rows']),len(model.T))):
   a=c['agc_awards'][g];e.need(a['up_mw'][t]==0 and a['down_mw'][t]==0,'stop conflicts with retained AGC award/stable obligation')
  v=model.stop[g,local];e.need(v.fixed and v.value in (0,1),'expected existing DA stop protection')
  v.fix(1);trace.append(dict(unit=g,absolute_tick=at,local_tick=local,old_protection=0,new_research_command=1,command_id=r['command_id'],source_pins=r['source_pins'],formal=False,safety_release=False))
 return trace

class ResearchStopPermissions:
 def __init__(self,host):
  e.need(isinstance(host,GeneralRollingHost),'live general host required');self.host=host;self.instructions=ResearchInstructions(host,research=True);self._records={}
  host.session.registry['source_pins'][str(Path(__file__))]=sha(__file__)
 def _at(self):
  e.need(self.host.pending is None,'register before building, never alter pending solve');return self.instructions._live_carry()['cutoff_tick']
 def _save(self,event,source,at,name,explicit):
  e.need(explicit is True and isinstance(name,str)and name.strip(),'separate explicit named research stop authorization required')
  e.need(event['phase']=='shutdown'and event['begin_tick']>=at+3,'only future accessible shutdown, no backfill')
  key=f"{event['unit']}:shutdown:{event['begin_tick']}";e.need(key not in self._records,'command immutable, no replacement')
  r=dict(command_id=key,unit=event['unit'],begin_tick=event['begin_tick'],rows=deepcopy(event['rows']),curve_sha256=e.digest(event['rows']),as_of_tick=at,declaration_name=name,source_pins=source['pins'],source_available_tick=source['available_tick'],source_kind=source['kind'],research_only=True,official=False,safety_release=False,actual_dispatch_authorized=False)
  e.need(r['source_available_tick']<=at,'future source unavailable');self._records[key]=r
  return deepcopy(r)
 def register_from_host(self,window,event_id,*,declaration_name,research_stop_override=False):
  at=self._at();plan=self.instructions._load(window);ev=plan['events'][event_id];latest,latestplan=self.instructions._latest_covering(ev['begin_tick'],at);e.need(latest==window and latestplan['events'].get(event_id)==ev,'stale source proposal')
  p,a=self.host.accepted_source(window);pins={str(f):sha(f)for f in [p/'SUMMARY.json',*p.glob('*.json'),*[a/s/f for s in ('SCUC','SCED')for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')]]}
  return self._save(ev,dict(pins=pins,available_tick=plan['available_tick'],kind='host_verified_RT_plan_simulated_instruction'),at,declaration_name,research_stop_override)
 def register_from_selected_da(self,unit,absolute_tick,*,declaration_name,research_stop_override=False,official=False):
  at=self._at();e.need(official is False,'official instruction import unsupported; publication-domain evidence is not dispatch authority')
  # Selected source is host context, never a user supplied path/JSON record.
  da=Path(self.host.agc.context['selected_DA']);self.host.agc.verify()
  import importlib.util
  from v6_host import INSTALL
  validator=INSTALL/'guangdong_rt_price_program_v22_20261112/gd_price/source_contracts.py'
  module_spec=importlib.util.spec_from_file_location('_stop_selected_DA_validator',validator);module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
  pins=module.selected_da(da);pins[str(validator)]=sha(validator);ar=read(da/'ARTIFACTS.json');casefile=da/ar['inputs']['case']['path'];e.need(sha(casefile)==self.host.session.registry['case_identity'],'same host seed case')
  case=self.host.session.program.engine.loads(casefile.read_bytes());n=read(da/'da_sced/full_names.json')['variables'];x=dict(zip(n,read(da/'da_sced/expanded_response.json')['primal']))
  e.need(type(absolute_tick)is int and 0<=absolute_tick<len(case['energy_inputs']['horizon']['periods']),'source horizon');e.need(x.get(f'stop[{unit},{absolute_tick}]')==1,'no actual selected DA stop event')
  rows=case['curves'][unit]['shutdown'];e.need(absolute_tick+len(rows)<=len(case['energy_inputs']['horizon']['periods']),'complete source curve')
  for j,row in enumerate(rows):e.need(abs(x[f'power[{unit},{absolute_tick+j}]']-row['mean'])<=1e-7,'raw DA curve mismatch')
  origin=case['contract']['day_start'];publication=case['contract']['publication_at'];available=e.tick(publication,origin)
  ev=dict(unit=unit,phase='shutdown',begin_tick=absolute_tick,rows=rows)
  return self._save(ev,dict(pins=pins,available_tick=available,kind='selected_DA_48h_research_extension_NOT_official_published_event'),at,declaration_name,research_stop_override)
 def apply(self,model):
  self.host.session.verify_sources()
  for r in self._records.values():
   for f,h in r['source_pins'].items():e.need(sha(f)==h,'stop permission source drift')
  return apply_records(model,self._records.values())
 def read(self):return dict(commands=deepcopy(list(self._records.values())),independent_authorization_required=True,formal=False,safety_release=False)

class StopPermissionHost(GeneralRollingHost):
 def __init__(self,*args,**kw):
  super().__init__(*args,**kw);self.stop_permissions=ResearchStopPermissions(self)
 def prepare(self):
  builder=self.session.builder;original=builder.serialize_model;trace=[]
  def guarded(model,options):
   trace.extend(self.stop_permissions.apply(model));return original(model,options)
  builder.serialize_model=guarded
  try:result=super().prepare()
  finally:builder.serialize_model=original
  path=self.pending/'STOP_PERMISSION_BINDING.json';path.write_text(json.dumps(dict(commands=self.stop_permissions.read(),applied=trace),sort_keys=True));self.hooks.prepared[str(self.pending.resolve())]['artifact_pins'][str(path)]=sha(path);return result
 def read(self):
  z=super().read();z['research_stop_permissions']=self.stop_permissions.read()if hasattr(self,'stop_permissions')else None;return z
