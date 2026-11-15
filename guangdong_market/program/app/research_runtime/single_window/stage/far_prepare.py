"""Planning-only scoped build. Never registers predicted carry in actual domain."""
from pathlib import Path
from copy import deepcopy
from dataclasses import asdict,is_dataclass
import json,sys,importlib.util,hashlib
from predicted_registry import verify_predicted_stage
from stage_api import configure
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def prepare_predicted_stage(*,program_root,hooks,predicted,output,loss_overlay=None):
 root=configure(program_root);r=verify_predicted_stage(hooks,predicted);z=r['projected'];s=hooks.session;output=Path(output).resolve();output.mkdir(exist_ok=False)
 ledger_id=hooks.predicted_stages[str(Path(predicted).resolve())]['ledger_id'];ledger=hooks.ledgers[ledger_id]
 c=read(r['origin_carry_path']);near=c['delivery_tick'];c.update({k:deepcopy(z[k])for k in ('thermal','thermal_age_h','fixed_curve_state','storage','notice_values')});c.update(boundary_at=z['boundary_at'],delivery_tick=z['projected_boundary_tick'],prediction_only=True,actual_execution_allowed=False)
 c['source_pins'].update(r['pins']);c['source_pins'][str(Path(predicted).resolve())]=sha(predicted)
 cp=output/'PREDICTED_INPUT.json';cp.write_text(json.dumps(c,indent=2));stage=dict(block='far',count=24,start_tick=c['delivery_tick'],near_start_tick=near,anchor_ticks=3,initial_kind='near_predicted_end23',carry_sha256=sha(cp),near_boundary_proof=dict(end_tick=c['delivery_tick'],SCED_raw_sha256=z['accepted_source']['SCED_raw_sha256'],carry_sha256=sha(cp)))
 oldpath=list(sys.path);saved={k:sys.modules.get(k)for k in ('day_contract','short_support')};import thermal_physical
 oldvalidate=thermal_physical.validate_contract;B=Path(__file__).parent
 try:
  for name in ('day_contract','short_support'):
   spec=importlib.util.spec_from_file_location(name,B/(name+'.py'));m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
  thermal_physical.validate_contract=sys.modules['short_support'].validate_contract
  spec=importlib.util.spec_from_file_location('_predicted_builder',B/'builder.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b);serialize=b.serialize_model;proof={}
  from host import event
  from availability import window_plan
  import pyomo.environ as pyo
  def guard(model,options):
   case=deepcopy(model._r23['inputs'])
   if is_dataclass(case['energy_inputs']['horizon']):case['energy_inputs']['horizon']=asdict(case['energy_inputs']['horizon'])
   proof['ledger']=event.apply_ledger_to_free_model(model,case,ledger,host_case_identity=s.registry['case_identity'],run_id=s.registry['run_id'],expected_ledger_sha256=ledger_id)
   rows,scheduled=window_plan(model._r23['inputs'],ledger,hooks._plan_views,cells=len(model.T),research=hooks.research)
   model.notice_availability_gate=pyo.ConstraintList()
   for row in rows:model.notice_availability_gate.add(model.start[row['unit'],row['local_tick']]==row['value'])
   proof.update(availability=rows,unissued_research_proposals=scheduled)
   return serialize(model,options)
  b.serialize_model=guard
  result=b.build(s.registry['seed'],cp,output/'prepared',stage=stage,loss_overlay=loss_overlay)
 finally:
  sys.path[:]=oldpath;thermal_physical.validate_contract=oldvalidate
  for k,v in saved.items():
   if v is None:sys.modules.pop(k,None)
   else:sys.modules[k]=v
 assert str(cp)not in hooks.carries and str(output/'prepared')not in hooks.prepared
 (output/'PLANNING_ONLY.json').write_text(json.dumps(dict(schema='gd.registered.predicted_stage.v1',execution_allowed=False,publication_allowed=False,actual_carry_registered=False,source=predicted,proof=proof,stage=stage),indent=2))
 return dict(result,prediction_only=True,actual_registration=False,native_calls=0)
