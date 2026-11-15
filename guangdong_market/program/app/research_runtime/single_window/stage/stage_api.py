"""Small adapters for existing RuntimeHooks and existing fixed-curve predictor.
No new host, source acceptance, native execution, notice publication or billing.
program_root is the installed app_copy directory containing app/ and engines/.
"""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
import sys,hashlib,importlib,math
_ROOT=None
B=Path(__file__).resolve().parent

def configure(program_root):
 global _ROOT
 root=Path(program_root).resolve()
 if not(root/'app').is_dir() or not(root/'engines/guangdong_short_unified_v6/rt').is_dir():raise ValueError('program_root must contain app and installed v6 engine')
 if _ROOT is not None and root!=_ROOT:raise ValueError('different program root: use a fresh process')
 _ROOT=root;rt=configured_rt()
 for p in [rt/'guangdong_rt_short_rolling_v2_20261112',rt/'guangdong_rt_short_midnight_v1_20261112',rt/'guangdong_rt_short_user_cli_v5_20261112',B]:sys.path.insert(0,str(p))
 return root

def configured_rt():
 if _ROOT is None:raise ValueError('configure explicit program_root first')
 return _ROOT/'engines/guangdong_short_unified_v6/rt'

def _prepare_registered_stage(*,program_root,seed,carry,stage,agc_source,output,hooks,ledger_id,loss_overlay=None):
 """Normal RuntimeHooks.build; preserves host registration and event-ledger checks.
 agc_source is explicit existing projection file or {'kind':'registered_host'}.
 Far projected boundaries must first be supported/registered by host, never auto-adopted.
 """
 root=configure(program_root)
 # Do not duck-type an uploaded object into source authority.
 mod=sys.modules.get('runtime_hooks')
 if mod is None or not isinstance(hooks,mod.RuntimeHooks):raise TypeError('existing initialized RuntimeHooks required')
 expected=root/'app/lifecycle_runtime/guangdong_event_host_candidate_v1_20261113/runtime_hooks.py'
 if Path(mod.__file__).resolve()!=expected.resolve():raise ValueError('RuntimeHooks belongs to different program root')
 s=hooks.session;s.verify_sources()
 if Path(seed).resolve()!=Path(s.registry['seed']).resolve():raise ValueError('seed differs from registered session')
 cp=Path(carry).resolve();hs=hashlib.sha256(cp.read_bytes()).hexdigest()
 if hooks.carries.get(str(cp))!=hs or stage['carry_sha256']!=hs:raise ValueError('unregistered or changed carry')
 if ledger_id not in hooks.ledgers:raise ValueError('unregistered notice ledger')
 if not isinstance(agc_source,dict)or agc_source.get('kind')not in ('registered_host','projection'):raise ValueError('explicit AGC source required')
 projection=None
 if agc_source['kind']=='projection':
  projection=Path(agc_source['path']).resolve()
  if hashlib.sha256(projection.read_bytes()).hexdigest()!=agc_source['sha256']:raise ValueError('AGC projection drift')
  if s.registry['source_pins'].get(str(projection))!=agc_source['sha256']:raise ValueError('AGC projection not session registered')
 import importlib.util
 for name in ('day_contract','short_support'):
  spec=importlib.util.spec_from_file_location(name,B/(name+'.py'));module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
 thermal=importlib.import_module('thermal_physical');thermal.validate_contract=sys.modules['short_support'].validate_contract
 spec=importlib.util.spec_from_file_location('_gd_explicit_stage_builder',B/'builder.py');candidate=importlib.util.module_from_spec(spec);spec.loader.exec_module(candidate)
 previous=s.builder;original_build=candidate.build
 def scoped_build(seed_path,carry_path,out):return original_build(seed_path,carry_path,out,stage=stage,agc_projection=projection,loss_overlay=loss_overlay)
 candidate.build=scoped_build;s.builder=candidate
 try:return hooks.build(cp,Path(output).resolve(),ledger_id=ledger_id)
 finally:s.builder=previous;candidate.build=original_build

def prepare_registered_stage(**kwargs):
 """Exception-safe scoped adapter; normal host builder/import context restored."""
 paths=list(sys.path);saved={k:sys.modules.get(k)for k in ('day_contract','short_support')};thermal=sys.modules.get('thermal_physical');validator=getattr(thermal,'validate_contract',None)
 try:return _prepare_registered_stage(**kwargs)
 finally:
  sys.path[:]=paths
  for k,v in saved.items():
   if v is None:sys.modules.pop(k,None)
   else:sys.modules[k]=v
  if thermal is not None and validator is not None:thermal.validate_contract=validator

def _predictor(program_root):
 root=configure(program_root);vendor=root/'app/lifecycle_runtime/guangdong_fixed_event_entry_v1_20261113/vendor';reader=configured_rt()/'guangdong_rt_short_state_review_20261112'
 for p in [reader,vendor]:sys.path.insert(0,str(p))
 m=importlib.import_module('exact_fixed')
 if Path(m.__file__).resolve()!=vendor/'exact_fixed.py':raise ValueError('predictor namespace from different program')
 return m

def predict_stage_end(*,program_root,case,incoming,names,primal,receipts,accepted_source,cell_count):
 """Arithmetic only. accepted_source must be supplied by verified host export.
 Uses existing exact_fixed age+thermal_prefix code; missing issued receipt rejects.
 Does NOT promote future unissued starts into receipts. Fixed single curve only.
 """
 if not accepted_source.get('SCED_raw_sha256') or not accepted_source.get('names_sha256'):raise ValueError('explicit accepted raw identities required')
 if type(cell_count)is not int or cell_count<=0:raise ValueError('positive prefix')
 if len(names['variables'])!=len(primal) or len(set(names['variables']))!=len(primal):raise ValueError('named vector mismatch')
 c=deepcopy(case)
 from dataclasses import is_dataclass,asdict
 if is_dataclass(c['energy_inputs']['horizon']):c['energy_inputs']['horizon']=asdict(c['energy_inputs']['horizon'])
 result=_predictor(program_root).derive(c,{'thermal':incoming['thermal'],'issued_notices':incoming.get('issued_notices',{})},names,primal,executed_cells=cell_count,receipts=receipts)
 values=dict(zip(names['variables'],primal));begin=datetime.fromisoformat(c['contract']['domain_start']);end=begin+timedelta(minutes=5*cell_count);storage={};deltas={}
 for uid,b in c.get('batteries',{}).items():
  eta=math.sqrt(b['roundtrip_efficiency']);mode=b['initial_mode'];age=b['initial_mode_age_h'];ledger=deepcopy(b['cycle_budget']);changes=[]
  for t in range(cell_count):
   ch=values[f'battery.charge[{uid},{t}]'];dis=values[f'battery.discharge[{uid},{t}]'];cm=values[f'battery.charge_mode[{uid},{t}]'];dm=values[f'battery.discharge_mode[{uid},{t}]']
   if any(abs(z-round(z))>1e-7 or round(z)not in(0,1)for z in (cm,dm))or round(cm)+round(dm)>1:raise ValueError('invalid storage mode')
   new='charge'if cm>.5 else'discharge'if dm>.5 else'idle'
   hour_key=(begin+timedelta(minutes=5*t)).replace(minute=0,second=0,microsecond=0).isoformat();direction_keys=[k for k in values if k.startswith('battery.hour_direction['+uid+',')and hour_key in k]
   if len(direction_keys)!=1:raise ValueError('missing hourly mode direction')
   direction=values[direction_keys[0]]
   if (cm>.5 and direction>.5)or(dm>.5 and direction<.5):raise ValueError('storage mode contradicts hourly direction')
   age=age+1/12 if new==mode else 1/12;mode=new;changes.append((eta*ch-dis/eta)/12)
   native=values[f'battery.energy[{uid},{t}]'];replay=math.fsum([b['initial_energy_mwh']]+changes)
   if abs(replay-native)>1e-7:raise ValueError('SOC replay mismatch')
   date=(begin+timedelta(minutes=5*t)).date().isoformat();ledger[date]['used_internal_mwh']+=(eta*ch+dis/eta)/12
  # Native endpoint is authoritative, never cumulative replay substitution.
  E=values[f'battery.energy[{uid},{cell_count-1}]']
  if not b['energy_min_mwh']<=E<=b['energy_max_mwh']:raise ValueError('native SOC outside hard bounds; explicit separate normalization needed')
  previous_hour=(end-timedelta(minutes=5)).replace(minute=0,second=0,microsecond=0);hour=end.replace(minute=0,second=0,microsecond=0)
  keys=[k for k in values if k.startswith('battery.hour_direction['+uid+',')and previous_hour.isoformat()in k]
  if len(keys)!=1:raise ValueError('hour direction source missing')
  storage[uid]=dict(initial_energy_mwh=E,initial_power_mw=dis-ch,initial_mode=mode,initial_mode_age_h=age,cycle_budget=ledger,initial_hour_direction=(1 if values[keys[0]]>.5 else -1)if previous_hour==hour else 0);deltas[uid]=math.fsum(changes)
 return dict(schema='gd.stage.predicted_end.v1',thermal=result['boundary_states'],thermal_age_h=result['thermal_age_h'],fixed_curve_state=result['fixed_curve_state'],storage=storage,notice_values=deepcopy(incoming['notice_values']),notice_requirements=result['notice_requirements'],future_unexecuted_plan=result['future_unexecuted_plan'],boundary_at=result['boundary_at'],predicted_cells=cell_count,actual_executed_end_exclusive=incoming['executed_end_exclusive'],actual_issued_end_exclusive=incoming['issued_end_exclusive'],projected_boundary_tick=int((end-datetime.fromisoformat(c['contract']['origin'])).total_seconds()/300),predicted_energy_delta_mwh=deltas,accepted_source=deepcopy(accepted_source),source_authority='caller verified host export; this function is arithmetic, not acceptance',new_receipts=0,actual_accounting_updated=False,publication=False,execution=False,native_calls=0)
