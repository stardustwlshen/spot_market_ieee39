"""Conservative startup availability gate; no fabricated 5-minute notice scheduler."""
from copy import deepcopy
from exact_fixed import validate_receipts
from control import e

def locks(case,ledger,*,cells):
 e.verify_ledger(ledger);cut=e.tick(case['contract']['cutoff'],case['contract']['origin']);available=e.tick(case['contract']['domain_start'],case['contract']['origin']);e.need(ledger['cutoff_tick']<=cut,'future instruction ledger at model cutoff');leads=validate_receipts(case,list(ledger['receipts'].values()),cut);out=[]
 for g,lead in leads.items():
  for t in range(cells):
   begin=available+t;due=begin-lead
   if cut<due<available:
    receipts=[r for r in ledger['receipts'].values()if r['unit']==g and r['phase']=='startup'and r['begin_tick']==begin]
    e.need(len(receipts)<=1,'ambiguous old obligation')
    out.append(dict(unit=g,local_tick=t,begin_tick=begin,notice_due_tick=due,value=int(bool(receipts)),reason='preserve trusted already-issued obligation'if receipts else'new accepted plan unavailable at notice deadline; nonaligned pending-notice scheduler unsupported'))
 return out

def window_plan(case,ledger,views,*,cells,research):
 rows=locks(case,ledger,cells=cells);cut=e.tick(case['contract']['cutoff'],case['contract']['origin']);commitments=[]
 for row in rows:
  if row['value']:continue
  begin=row['begin_tick'];eligible=[(p['available_tick'],w,p)for w,p in views.items()if p['available_tick']<=cut and p['domain_start_tick']<=begin<p['domain_start_tick']+p['cells']]
  if not eligible:continue
  _,stage,plan=max(eligible,key=lambda x:(x[0],x[1]));key=f"{row['unit']}:startup:{begin}";event=plan['events'].get(key)
  if event is None:continue
  e.need(research is True,'explicit research switch required for scheduled notice commitment')
  e.need(event['notice_tick']==row['notice_due_tick']and event['rows']==case['curves'][row['unit']]['startup'],'old-plan curve/deadline changed')
  command=dict(event=deepcopy(event),source_stage=stage,source_plan_sha256=plan['plan_sha256'],source_bundle_sha256=plan['bundle_sha256'],source_available_tick=plan['available_tick'],as_of_tick=cut,promised_issue_tick=row['notice_due_tick'],issued=False,kind='scheduled_research_commitment_not_issued_receipt',formal=False)
  commitments.append(command);row.update(value=1,reason='precommitted from latest old plan already available at model cutoff')
 return rows,commitments
