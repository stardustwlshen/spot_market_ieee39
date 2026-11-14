"""Only the bounded old-cutoff -> accepted-availability intermediate due ticks."""
from control import e,sha

def issue_scheduled(ledger,scheduled,plans,pins,*,cut,available,current_stage,research):
 transitions=[]
 for due in sorted({x['promised_issue_tick']for x in scheduled}):
  e.need(cut<due<available and research is True,'invalid intermediate notice clock/permission')
  for source in sorted({x['source_stage']for x in scheduled if x['promised_issue_tick']==due}):
   e.need(source<current_stage,'new plan cannot decide past notice');bundle,h=plans[source]
   for f,v in pins[source].items():e.need(sha(f)==v,'scheduled source changed')
   selected=e.export_plan(bundle,expected_bundle_sha256=h);items=[x for x in scheduled if x['promised_issue_tick']==due and x['source_stage']==source]
   e.need(selected['available_tick']<=cut,'old source not yet available at commitment cutoff')
   e.need(all(x['source_bundle_sha256']==h and x['source_plan_sha256']==selected['plan_sha256']and x['as_of_tick']==cut and x['source_available_tick']==selected['available_tick'] for x in items),'untrusted scheduled source/clock')
   for x in items:e.need(selected['events'].get(x['event']['event_id'])==x['event'],'scheduled event changed')
   previous=ledger;ledger=e.publish(bundle,ledger,expected_bundle_sha256=h,cutoff_tick=due,simulate=True,expected_previous_ledger_sha256=ledger['ledger_sha256'])
   for x in items:
    receipt=ledger['receipts'].get(x['event']['event_id']);e.need(receipt is not None and receipt['issued_tick']==due,'not issued at exact due tick')
   transitions.append(dict(due_tick=due,old_source_stage=source,source_bundle_sha256=h,new_receipts=sorted(set(ledger['receipts'])-set(previous['receipts'])),new_plan_used=False))
 return ledger,transitions
