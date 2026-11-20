"""Optional normal-host callbacks. Trust anchors are kept in host memory, not user JSON.
No solver or publication permission. Caller owns validated accepted source registration.
"""
from pathlib import Path
from copy import deepcopy
from dataclasses import asdict,is_dataclass
import hashlib,json
from host import Session,sha,read,write,event

class RuntimeHooks:
 def __init__(self,session):
  if not isinstance(session,Session):raise TypeError('registered event host Session required')
  self.session=session;self.ledgers={};self.carries={};self.prepared={}
 def adopt_registered_boundary(self,w):
  """Only predetermined trusted source registrations, no arbitrary upload path."""
  if hasattr(self.session,'registered_boundary'):
   p,ledger=self.session.registered_boundary(w);self.carries[str(p)]=sha(p);self.ledgers[ledger['ledger_sha256']]=deepcopy(ledger);return p,ledger['ledger_sha256']
  r=self.session.registry['windows'][str(w)];self.session.bundle(w)
  p=Path(r['prepared'])/'CARRY.json';self.carries[str(p)]=sha(p)
  ledger=self.session._ledger(w);self.ledgers[ledger['ledger_sha256']]=deepcopy(ledger)
  return p,ledger['ledger_sha256']
 def build(self,carry_path,output,*,ledger_id):
  """Normal full-builder path at the supplied host-registered NEW physical boundary."""
  s=self.session;s.verify_sources();carry_path=Path(carry_path);assert str(carry_path)in self.carries and sha(carry_path)==self.carries[str(carry_path)],'unregistered physical boundary';assert ledger_id in self.ledgers,'unregistered notice ledger'
  ledger=self.ledgers[ledger_id];serialize=s.builder.serialize_model;proof={}
  def hook(model,options):
   case=deepcopy(model._r23['inputs'])
   h=case['energy_inputs']['horizon']
   if is_dataclass(h):case['energy_inputs']['horizon']=asdict(h)
   proof.update(event.apply_ledger_to_free_model(model,case,ledger,host_case_identity=s.registry['case_identity'],run_id=s.registry['run_id'],expected_ledger_sha256=ledger_id))
   return serialize(model,options)
  s.builder.serialize_model=hook
  try:result=s.builder.build(s.registry['seed'],carry_path,output)
  finally:s.builder.serialize_model=serialize
  output=Path(output).resolve();summary=read(output/'SUMMARY.json');summary['source_pins'].update(s.registry['source_pins']);summary['source_pins'][str(Path(__file__).resolve())]=sha(__file__)
  # New candidate only, producer outputs are ours. Replace metadata to include hook,
  # never touch underlying original accepted evidence.
  (output/'SUMMARY.json').write_text(json.dumps(summary,sort_keys=True,allow_nan=False))
  self.prepared[str(output)]={'summary_sha256':sha(output/'SUMMARY.json'),'ledger_id':ledger_id,'carry_path':str(carry_path),'carry_sha256':sha(carry_path),'artifact_pins':{str(p):sha(p)for p in output.glob('*.json')}}
  return dict(result,event_hook=proof,registered_for_actual_acceptance=True,native_calls=0)
 def handoff_accepted(self,prepared,actual,output,*,simulate_no_deviation=False):
  """Once normal runner has REAL accepted SCUC+SCED, replay without stable-only code.
 Not exercised with a newly solved event-bearing IEEE39 pair in this release.
 """
  assert simulate_no_deviation is True,'explicit simulated handoff required'
  s=self.session;s.verify_sources();p=Path(prepared).resolve();a=Path(actual).resolve();record=self.prepared[str(p)]
  for f,h in record['artifact_pins'].items():assert sha(f)==h,'generated input changed'
  assert sha(record['carry_path'])==record['carry_sha256'],'boundary changed'
  from host_export import export
  summary=read(p/'SUMMARY.json')
  if hasattr(s,'export_generated_pair'):bundle,h=s.export_generated_pair(p,a,record)
  else:bundle,h=export(p,a,producer_root=s.registry['accepted_producer_root'],expected_summary_sha256=record['summary_sha256'],case_identity=s.registry['case_identity'],run_id=s.registry['run_id'],available_at=summary['domain_start'])
  ledger=self.ledgers[record['ledger_id']];new=event.carry_candidate(bundle,ledger,expected_bundle_sha256=h,expected_ledger_sha256=record['ledger_id'])
  incoming=read(record['carry_path']);carry=deepcopy(incoming);carry.update(thermal=new['boundary_states'],thermal_age_h=new['thermal_age_h'],storage=new['storage'],notice_values=new['notice_values'],notice_requirements=new['notice_requirements'],fixed_curve_state=new['fixed_curve_state'],cutoff=new['cutoff'],boundary_at=new['boundary_at'],executed_end_exclusive=new['executed_end_exclusive'],issued_end_exclusive=new['issued_end_exclusive'],cutoff_tick=new['executed_end_exclusive'],delivery_tick=new['issued_end_exclusive'],formal=False,safety_release=False)
  assert not new['power_normalization']and not new['energy_normalization'],'requires explicit normalization receipt adapter'
  carry['source_pins']={**s.registry['source_pins'],str(Path(__file__).resolve()):sha(__file__),**record['artifact_pins'],str(a/'ACCEPTED_PAIR.json'):sha(a/'ACCEPTED_PAIR.json')}
  carry['boundary_numeric_normalization']=[];carry['energy_boundary_numeric_normalization']=[]
  # Publish only newly due research notices, then bind exactly the same ledger
  # into the next builder's legacy notice map and additive hook.
  nextledger=event.publish(bundle,ledger,expected_bundle_sha256=h,cutoff_tick=new['executed_end_exclusive'],simulate=True,expected_previous_ledger_sha256=record['ledger_id'])
  carry['notice_values']={g:{str(r['notice_tick']):1 for r in nextledger['receipts'].values()if r['unit']==g and r['phase']=='startup'}for g in carry['thermal']}
  from gd_forecast_inputs import retain_forecast_pin
  retain_forecast_pin(s.run/'seed',s.context['seed_pins'],incoming['source_pins'],carry['source_pins'])
  carry['event_ledger_sha256']=nextledger['ledger_sha256']
  output=Path(output).resolve();write(output,carry);self.carries[str(output)]=sha(output)
  self.ledgers[nextledger['ledger_sha256']]=nextledger
  return dict(carry_path=str(output),carry_sha256=sha(output),ledger_id=nextledger['ledger_sha256'],native_calls=0,formal=False,safety_release=False)
