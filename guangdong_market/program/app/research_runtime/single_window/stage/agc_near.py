"""Near24 consumer adapted from existing AgcLifecycle; same issuer gates."""
import json
from datetime import timedelta
from agc_lifecycle import AgcLifecycle,need,stamp,digest,sha,apply_available
def apply_near(self,kw,carry):
 need(type(self)is AgcLifecycle,"registered AgcLifecycle required")
 n=len(kw["energy_inputs"]["horizon"].ids);need(n==24,"near24 only")
 self.verify();need(carry['seed_case_sha256']==self.seed_sha,'wrong consumer seed');cut=stamp(carry['cutoff']);begin=stamp(kw['contract']['domain_start']);need(begin==cut+timedelta(minutes=15),'issued prefix/domain mismatch');end=begin+timedelta(minutes=5*n);receipts=[]
 for h in range(24):
  start=self.origin+timedelta(hours=h)
  if start+timedelta(hours=1)>begin and start<end and start-timedelta(minutes=30)<=cut:receipts.append(self.produce(h,cutoff=cut.isoformat()))
 thermal={g:dict(maximum=[d['max_mw'][t]for t in range(n)],minimum=[d['min_mw'][t]for t in range(n)])for g,d in kw['energy_inputs']['thermal'].items()};c=dict(branch_id=self.context['run_id'],seed_case_sha256=self.seed_sha,cutoff=cut.isoformat(),delivery=begin.isoformat(),domain_start=begin.isoformat(),cells=n,thermal=thermal,zone='explicit_single_research_zone',positive_reserve_mw=[kw['reserve']['positive_mw'][t]for t in range(n)],accident_reserve_mw=[kw['reserve']['contingency_mw'][t]for t in range(n)])
 base=json.loads(json.dumps(kw['agc_awards']));z=apply_available(receipts,c,base,expected_receipt_digests=[digest(r)for r in receipts],expected_consumer_digest=digest(c));need(z['agc']==base,'award changed: new solve/security lifecycle required, cannot reuse old raw');new={g:{**a,**{side:{int(t):v for t,v in a[side].items()}for side in ('up_mw','down_mw')}}for g,a in z['agc'].items()};kw['agc_awards'].clear();kw['agc_awards'].update(new)
 from safety_adapter import pending_safety
 binding=dict(safety=pending_safety(),issuer_receipt_sha256=[digest(r)for r in receipts],branch_sha256=digest({'run_id':self.context['run_id'],'seed':self.seed_sha}),period_sha256=digest({'cutoff':cut.isoformat(),'domain_start':begin.isoformat()}),run_id=self.context['run_id'],consumer_sha256=digest(c),cutoff=cut.isoformat(),domain_start=begin.isoformat(),receipt_files={str(self.root/f'H{int((stamp(r["context"]["hour_start"])-self.origin).total_seconds()//3600):02}.json'):sha(self.root/f'H{int((stamp(r["context"]["hour_start"])-self.origin).total_seconds()//3600):02}.json')for r in receipts},applied=z['applied_receipts'],same_original_awards=True,already_consumed_by_old_SCED=False,H0_issued_first15min_untouched=True,native_calls=0,formal=False,safety_release=False)
 return kw,binding
