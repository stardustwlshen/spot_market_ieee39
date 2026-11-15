"""Fresh provincial base from accepted new DA; never promotes historical DA evidence."""
from pathlib import Path
from copy import deepcopy
import json,hashlib,shutil,uuid
from datetime import datetime,timedelta
from gd_provincial_da_contract import verify,read
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x') as f:json.dump(z,f,indent=2,allow_nan=False)
def initialize(*,source_base,da_manifest,output,program,research=False):
 if research is not True:raise ValueError('explicit research initialization required')
 src=Path(source_base).resolve();mp=Path(da_manifest).resolve();out=Path(output).resolve()
 proof=verify(mp,require_price=True);manifest=read(mp);da=mp.parent
 if out.exists():raise ValueError('new base only; never overwrite')
 # The archived scenario seed is data lineage only, not the new DA acceptance.
 old=read(src/'SHORT_CONTEXT.json')
 for f,h in old['seed_pins'].items():
  if sha(f)!=h:raise ValueError('scenario seed source changed: '+f)
 out.mkdir(parents=True);shutil.copytree(src/'seed',out/'seed')
 from smm.gd_october_job_codec import loads
 case=loads((out/'seed/case.json').read_bytes());request=read(out/'seed/request.json')
 run_id='provincial-rt-'+uuid.uuid4().hex;request['run_id']=run_id
 request['case_sha256']=sha(out/'seed/case.json')
 request['sources']['AGC']['sha256']=sha(out/'seed/agc.json')
 stage=manifest['stages']['SCED'];warm=dict(names=stage['names']['path'],names_sha256=stage['names']['sha256'],response=stage['response']['path'],response_sha256=stage['response']['sha256'],schema='gd.provincial.da_reference.v1',grid_minutes=15,scenario_seed_is_not_DA_case=True)
 (out/'seed/WARMSTART_SOURCE.json').write_text(json.dumps(warm))
 physical=read(manifest['physical_solution']['path']);cells=physical['physical_cells'];origin=cells[0]['start'];date=datetime.fromisoformat(origin).date().isoformat()
 if request['origin']!=origin:raise ValueError('scenario/DA origin mismatch')
 request.update(grid_minutes=15,rt_clock_mode='provincial_15min_execution',delivery_tick=0,cutoff_tick=0,horizon_cells=96,near_cells=8,far_cells=0,objective_policy='eight15min rolling window first cell executed',tail_policy='explicit declared daily targets',DA_shutdown_policy='inherit new provincial DA thermal combination',DA_online={g:[bool(row['thermal'][g]['connected'])for row in cells]for g in case['energy_inputs']['thermal']},DA_acceptance_sha256=sha(da/'VERIFIED_DISPATCH.json'))
 request['sources']['DA']=dict(available_at=request['sources']['DA']['available_at'],sha256=sha(mp),source='new provincial DA SCUC SCED PRICE verified manifest',uses_future_actual=False)
 request['state']=dict(basis='declared_initial_state_not_executed_DA',case_sha256=sha(out/'seed/case.json'),completed_window=-1,executed_end_tick=0,issued_end_tick=0,fixed_values={},notice_values={g:{}for g in case['energy_inputs']['thermal']},initial_phase_source='declared actual model initial state at t0',receipt_at=origin,run_id=run_id)
 request['state']['receipt_sha256']=hashlib.sha256(json.dumps(request['state'],sort_keys=True).encode()).hexdigest()
 (out/'seed/request.json').write_text(json.dumps(request))
 from gd_live_market_runtime.initial_state import from_declared
 prior=read(src/'INITIAL_CARRY.json');carry=from_declared(case,prior)
 pins={str(mp):sha(mp),**manifest['source_pins'],str(out/'seed/case.json'):sha(out/'seed/case.json')}
 forecast=out/'seed/FORECAST_SCENARIO.json'
 if forecast.exists():pins[str(forecast)]=sha(forecast)
 artifact=dict(thermal_online={g:[int(row['thermal'][g]['connected'])for row in cells]for g in case['energy_inputs']['thermal']},storage={})
 da_case=loads(Path(manifest['model_case']['path']).read_bytes())
 for g,z in da_case['batteries'].items():
  artifact['storage'][g]=dict(day_end_at=(datetime.fromisoformat(origin)+timedelta(days=1)).isoformat(),energy_mwh=z['terminal_energy_mwh'],limit_cycles=carry['storage'][g]['cycle_budget'][date]['limit_cycles'])
 def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
 available=da_case['contract']['publication_at']
 days=dict(schema='gd.short.day_declarations.research.v1',mode='hypothetical_published_research',grid_minutes=15,days={date:dict(available_at=available,published_at=available,basis='explicit_new_research_assumption',source=dict(case_sha256=sha(out/'seed/case.json'),accepted_original_DA_response_sha256=warm['response_sha256'],provincial_DA_manifest=str(mp)),artifact=artifact,artifact_sha256=digest(artifact))})
 dp=out/'DAYS.json';write(dp,days);pins[str(dp)]=sha(dp)
 carry.update(selected_DA=str(da),selected_DA_run_id=proof['run_id'],seed_case_sha256=sha(out/'seed/case.json'),seed_request_sha256=sha(out/'seed/request.json'),day_declaration_path=str(dp),day_declaration_sha256=sha(dp),source_pins=pins,provincial_DA_manifest=str(mp),provincial_DA_manifest_sha256=sha(mp))
 from gd_provincial15_model.pumped_base import attach_initial
 carry=attach_initial(carry,da_case,manifest,out)
 write(out/'INITIAL_CARRY.json',carry)
 context=dict(schema='gd.provincial.short.context.v1',rt_clock_mode='provincial_15min_execution',grid_minutes=15,run_id=run_id,selected_DA=str(da),selected_DA_run_id=proof['run_id'],provincial_DA_manifest=str(mp),provincial_DA_manifest_sha256=sha(mp),code_pins=program.pins(),seed_pins={str(p):sha(p)for p in [*sorted((out/'seed').glob('*.json')),out/'INITIAL_CARRY.json',dp]},event_model='declared_DA_combination_15min',start_window=0,formal=False)
 write(out/'SHORT_CONTEXT.json',context)
 write(out/'NEW_INITIALIZATION_RECEIPT.json',dict(schema='gd.provincial.initial_base.v1',grid_minutes=15,executed_end_exclusive=0,issued_end_exclusive=0,old_DA_reused=False,scenario_source=str(src),DA_source=proof,native_calls=0,formal=False))
 return dict(run=str(out),native_calls=0,run_id=run_id)
