"""Independent explicit-normalization event handoff; original hooks remain frozen."""
from normalization import *
from runtime_hooks import RuntimeHooks
from host import event
class NormalizedHooks(RuntimeHooks):
 @classmethod
 def attach(cls,hooks):
  e.need(type(hooks)is RuntimeHooks,'only original live registered hooks supported')
  hooks.session.verify_sources();out=cls(hooks.session)
  out.ledgers=hooks.ledgers;out.carries=hooks.carries;out.prepared=hooks.prepared
  out.session.registry['source_pins'][str(Path(__file__).resolve())]=sha(__file__)
  return out
 def handoff_accepted(self,prepared,actual,output,*,simulate_no_deviation=False):
  """Once normal runner has REAL accepted SCUC+SCED, replay without stable-only code.
 Independent normalization adapter; no new native call or real dispatch permission.
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
  normalized,norm=verify_bundle_storage(bundle);assert normalized==carry['storage'],'carry normalization differs'
  normpath=Path(output).resolve().with_suffix('.NORMALIZATION.json');write(normpath,norm)
  carry['source_pins']={**s.registry['source_pins'],str(Path(__file__).resolve()):sha(__file__),**record['artifact_pins'],str(a/'ACCEPTED_PAIR.json'):sha(a/'ACCEPTED_PAIR.json')}
  carry['boundary_numeric_normalization']=norm['power'];carry['energy_boundary_numeric_normalization']=norm['energy']
  carry['boundary_normalization_receipt']={'path':str(normpath),'sha256':sha(normpath),'receipt_sha256':norm['receipt_sha256']}
  carry['source_pins'].update({str(normpath):sha(normpath),**norm['source_pins']})
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
