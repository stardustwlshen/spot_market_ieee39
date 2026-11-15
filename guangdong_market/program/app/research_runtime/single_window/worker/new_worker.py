"""Fresh registered-source research window; prepare stops before native.
Supports the installed host's declared initialized scenario, not arbitrary files.
"""
from pathlib import Path
from copy import deepcopy
import sys,json,hashlib,importlib.util,traceback
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run_new_window(*,program_root,base_run,stage_api_root,price_api_root,selected_DA,research_agc_input,output,mode='research_prepare',execute_native=False,event_price_profile=None,reactive_profile=None):
 if mode not in ('research_prepare','research_native'):raise ValueError('unknown research mode')
 if mode=='research_native' and execute_native is not True:raise PermissionError('new native window needs explicit authorization; maximum five solves')
 if reactive_profile is not None:raise ValueError('reactive profiles are outside active_power_dc model scope; no AC or reactive control is run')
 root=Path(program_root).resolve();run=Path(base_run).resolve();api=Path(stage_api_root).resolve();price_api=Path(price_api_root).resolve();out=Path(output).resolve()
 if out.exists():raise ValueError('fresh output only; preserve existing raw and inspect')
 out.mkdir(parents=True);paths=list(sys.path);app=root/'app';rt=app/'lifecycle_runtime'
 try:
  sys.path[:0]=[str(app),str(rt),str(rt/'guangdong_general_event_runner_v1_20261113'),str(rt/'guangdong_unified_event_agc_host_v1_20261113'),str(api)]
  from runner import GeneralRollingHost
  from stage_api import prepare_registered_stage
  host=GeneralRollingHost(run,out/'host',mode='native'if mode=='research_native'else'pure',research=True,research_event_simulation=True);host.session.verify_sources()
  if Path(selected_DA).resolve()!=Path(host.session.context['selected_DA']).resolve():raise ValueError('selected DA mismatch')
  before=(deepcopy(host.hooks.carries),deepcopy(host.hooks.ledgers),host.window,str(host.cp),host.lid);c=read(host.cp);stage=dict(block='near',count=24,start_tick=c['delivery_tick'],near_start_tick=c['delivery_tick'],anchor_ticks=3,initial_kind='issued_prefix_projected_boundary',executed_end_exclusive=c['executed_end_exclusive'],issued_end_exclusive=c['issued_end_exclusive'],carry_sha256=sha(host.cp))
  import thermal_physical
  original=thermal_physical.build_free_scuc
  def agc(**kw):
   from agc_near import apply_near
   apply_near(host.agc,kw,c);return original(**kw)
  thermal_physical.build_free_scuc=agc;p=out/'near/prepared';p.parent.mkdir()
  try:near=prepare_registered_stage(program_root=root,seed=host.session.registry['seed'],carry=host.cp,stage=stage,agc_source={'kind':'registered_host'},output=p,hooks=host.hooks,ledger_id=host.lid)
  finally:thermal_physical.build_free_scuc=original
  result=dict(status='fresh_research_near_prepared',mode=mode,near=near,native_calls=0,next_step='SCUC native requires explicit authorization',execution=False,publication=False,settlement=False,full_window_AC=False,model_scope='active_power_dc',ac_assessment='not_in_model_scope',reactive_power_modeled=False,safety_release=False)
  if mode=='research_native':
   from native_runtime import execute_pair
   host.pending=p;near_accept=execute_pair(host.hooks,p,execute_native=True)
   sys.path.insert(0,str(api/'price_api'));from window_api_corrected import continue_window
   far=continue_window(program_root=root,host=host,near_prepared=p,near_actual=p.parent/'actual',research_agc_input=research_agc_input,output=out/'far_window',mode='research_native',execute_far=True)
   def independent_price():
    sys.path.insert(0,str(price_api));spec=importlib.util.spec_from_file_location('_new_window_price_api',price_api/'single_price.py');price=importlib.util.module_from_spec(spec);spec.loader.exec_module(price)
    price.prepare_eight_quarters(program_root=root,session=host.session,hooks=host.hooks,near_prepared=p,near_actual=p.parent/'actual',base_run=run,selected_DA=selected_DA,output=out/'price8',event_profile=event_price_profile)
    return price.execute_eight_quarters(session=host.session,prepared=out/'price8',output=out/'price8_actual',execute_native=True)
   priced=independent_price()
   result.update(status='fresh_research_window_numerically_accepted',near_acceptance=near_accept,far=far,price=priced,native_calls=5,price_quality=priced.get('price_quality'),formal_price_usable=False,full_window_AC=False,AC_calls=0,PRICE_skipped_for_safety=False,new_native_calls=5,PRICE_native_calls=1,automatic_retry=False,accepted_raw_paths=dict(near_SCUC=str(p.parent/'actual/SCUC/response.json.gz'),near_SCED=str(p.parent/'actual/SCED/response.json.gz'),far_SCUC=str(out/'far_window/far/actual/SCUC/response.json.gz'),far_SCED=str(out/'far_window/far/actual/SCED/response.json.gz')))
  if before!=(host.hooks.carries,host.hooks.ledgers,host.window,str(host.cp),host.lid):raise AssertionError('actual state advanced')
  result['actual_domain_unchanged']=True;(out/'RESULT.json').write_text(json.dumps(result,indent=2));return result
 finally:sys.path[:]=paths
