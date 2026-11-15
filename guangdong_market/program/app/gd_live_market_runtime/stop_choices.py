"""Read-only selected-DA source inventory. A choice is not a simulated instruction."""
from pathlib import Path
import importlib.util
from source import read,sha,e
from v6_host import INSTALL

def inspect(session,*,windows=4):
 from hour_scope import plan
 plan(windows)
 session.verify_sources()
 if session.context.get('schema')=='gd.provincial.short.context.v1':
  from gd_provincial_da_contract import verify,read as read_da
  mp=Path(session.context['provincial_DA_manifest']);verify(mp,require_price=True);m=read_da(mp)
  return dict(selected_DA=session.context['selected_DA'],as_of_tick=read(session.run/'INITIAL_CARRY.json')['cutoff_tick'],events=[],selectable_count=0,source_pins={str(mp):sha(mp),**m['source_pins']},official_import_supported=False,automatic_authorization=False,default_native_calls=0,policy='thermal combination inherited from new DA; RT stop override disabled')
 da=Path(session.context['selected_DA']);validator=INSTALL/'guangdong_rt_price_program_v22_20261112/gd_price/source_contracts.py'
 spec=importlib.util.spec_from_file_location('_stop_ui_source_validator',validator);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);pins=m.selected_da(da)
 ar=read(da/'ARTIFACTS.json');casefile=da/ar['inputs']['case']['path'];e.need(sha(casefile)==session.registry['case_identity'],'selected DA and host seed case mismatch')
 case=session.program.engine.loads(casefile.read_bytes());n=read(da/'da_sced/full_names.json')['variables'];x=dict(zip(n,read(da/'da_sced/expanded_response.json')['primal']));agc=read(session.run/'seed/agc.json');clock=read(session.run/'INITIAL_CARRY.json')['cutoff_tick'];events=[]
 for g in case['energy_inputs']['thermal']:
  curve=case['curves'][g]['shutdown']
  for tick in range(len(case['energy_inputs']['horizon']['periods'])):
   if x.get(f'stop[{g},{tick}]')!=1:continue
   reasons=[]
   if tick<clock+3:reasons.append('already issued or inaccessible at current clock')
   if not(clock+3<=tick and tick+len(curve)<=clock+3*windows+48):reasons.append('outside current planned model coverage; not a selectable event')
   full=tick+len(curve)<=len(case['energy_inputs']['horizon']['periods'])
   if not full or not all(abs(x.get(f'power[{g},{tick+j}]',float('inf'))-row['mean'])<=1e-7 for j,row in enumerate(curve)):reasons.append('no complete same-source raw curve witness')
   if any(agc[g][side].get(str(t),None)!=0 for side in ('up_mw','down_mw')for t in range(tick,tick+len(curve))):reasons.append('retained AGC award/stable obligation conflict or unavailable award')
   events.append(dict(unit=g,absolute_tick=tick,curve_sha256=e.digest(curve),curve=curve,source_available_tick=e.tick(case['contract']['publication_at'],case['contract']['day_start']),source_kind='selected_DA_48h_research_extension_NOT_official_published_event',selectable=not reasons,reasons=reasons))
 return dict(selected_DA=str(da),as_of_tick=clock,events=events,selectable_count=sum(v['selectable']for v in events),source_pins=pins,official_import_supported=False,automatic_authorization=False,default_native_calls=0)

def validate_request(inventory,request):
 if request is None:return None
 e.need(set(request)=={'unit','absolute_tick','declaration_name','research_stop_override'},'only explicit named research stop fields')
 e.need(request['research_stop_override']is True and isinstance(request['declaration_name'],str)and request['declaration_name'].strip(),'independent explicit stop permission required')
 choices=[v for v in inventory['events']if v['unit']==request['unit']and v['absolute_tick']==request['absolute_tick']]
 e.need(len(choices)==1 and choices[0]['selectable'],'no eligible selected-source event in this live scope; do not insert an external tail event')
 return dict(request)
