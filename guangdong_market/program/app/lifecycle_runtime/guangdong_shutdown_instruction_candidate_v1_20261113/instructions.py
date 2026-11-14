"""Host-owned simulated shutdown instructions. No real dispatch authority or solver."""
from pathlib import Path
from copy import deepcopy
import sys,uuid
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_event_host_candidate_v1_20261113'))
from v6_host import e,read,sha
class ResearchInstructions:
 def __init__(self,host,*,research=False):
  sys.path.insert(0,str(R/'guangdong_unified_event_agc_host_v1_20261113'))
  from unified import RollingHost
  e.need(research is True and isinstance(host,RollingHost),'live host + explicit research authorization required')
  self.host=host;c=self._live_carry();e.need(all(x['phase'] in ('stable','offline','off')and not x['remaining_curve'] for x in c['thermal'].values()),'active initial event import unsupported')
  self.host=host;self._commands={};self._clock=c['cutoff_tick']
 def _live_carry(self):
  self.host.session.verify_sources();p=Path(self.host.cp)
  e.need(str(p)in self.host.hooks.carries and sha(p)==self.host.hooks.carries[str(p)],'unregistered or changed live carry clock')
  return read(p)
 def _load(self,w):
  p,a=self.host.accepted_source(w);record=self.host.hooks.prepared[str(p.resolve())]
  b,h=self.host.session.export_generated_pair(p,a,record)
  return e.export_plan(b,expected_bundle_sha256=h)
 def _latest_covering(self,begin_tick,at):
  # Enumerate the live host registry, never trust the caller's selected window
  # or the token's original source as the latest forecast.
  candidates=[]
  for w in tuple(self.host.accepted):
   plan=self._load(w)
   if plan['available_tick']<=at and plan['domain_start_tick']<=begin_tick<plan['domain_start_tick']+plan['cells']:
    candidates.append((plan['available_tick'],w,plan))
  e.need(bool(candidates),'no currently available accepted plan covers event')
  _,w,plan=max(candidates,key=lambda x:(x[0],x[1]))
  return w,plan
 def register(self,w,event_id,*,as_of_tick,research=False):
  e.need(research is True,'explicit research instruction required');e.need(as_of_tick==self._live_carry()['cutoff_tick'],'as_of must equal live host cutoff; no backfill/future clock')
  plan=self._load(w);proposal=_select(plan,event_id,as_of_tick)
  latest,plan=self._latest_covering(proposal['event']['begin_tick'],as_of_tick)
  e.need(w==latest,'stale source window; register latest available covering plan')
  key=uuid.uuid4().hex
  self._commands[key]=_select(plan,event_id,as_of_tick);self._commands[key]['window']=w
  return key
 def cancel(self,token,*,as_of_tick):
  self._advance(as_of_tick);q=self._commands[token];e.need(not q['issued'],'issued instruction immutable');del self._commands[token]
 def _advance(self,at):
  e.need(type(at)is int and at>=self._clock and at==self._live_carry()['cutoff_tick'],'clock reversal/backfill/future');self._clock=at
 def issue(self,token,*,as_of_tick,research=False):
  e.need(research is True,'explicit simulated issue required');self._advance(as_of_tick);q=self._commands[token];e.need(not q['issued'],'already issued; immutable')
  latest,plan=self._latest_covering(q['event']['begin_tick'],as_of_tick);_revalidate(q,plan,as_of_tick)
  ledger=deepcopy(self.host.hooks.ledgers[self.host.lid]);e.verify_ledger(ledger);e.need(ledger['cutoff_tick']<=as_of_tick,'future ledger')
  e.need(all(ledger[k]==plan[k]for k in ('run_id','origin','case_identity')),'wrong lifecycle')
  _check_issued(ledger,plan)
  receipt=_receipt(q,plan,as_of_tick,token);ledger['receipts'][q['event']['event_id']]=receipt;ledger['pending'].pop(q['event']['event_id'],None);ledger['cutoff_tick']=as_of_tick
  ledger['ledger_sha256']=e.digest({k:v for k,v in ledger.items()if k!='ledger_sha256'});e.verify_ledger(ledger)
  self.host.hooks.ledgers[ledger['ledger_sha256']]=ledger;self.host.lid=ledger['ledger_sha256'];q['issued']=True
  return dict(receipt=receipt,ledger=ledger,formal=False,safety_release=False,actual_dispatch_authorized=False,native_calls=0)

def _select(plan,event_id,at):
 e.need(type(at)is int and at>=plan['available_tick'],'plan unavailable')
 ev=plan['events'].get(event_id);e.need(ev is not None and ev['phase']=='shutdown','same-source shutdown event required')
 e.need(at<=ev['begin_tick'],'past event; cannot backfill')
 return dict(event=deepcopy(ev),plan_sha256=plan['plan_sha256'],identity={k:plan[k]for k in ('run_id','case_identity','origin')},registered_at_tick=at,issued=False)
def _revalidate(command,plan,at):
 e.need(plan['available_tick']<=at<=command['event']['begin_tick'] and at>=command['registered_at_tick'],'unavailable/late/backdated issue')
 e.need(command['identity']=={k:plan[k]for k in command['identity']},'source identity changed')
 e.need(plan['events'].get(command['event']['event_id'])==command['event'],'unissued proposal superseded; register current accepted event')
def _check_issued(ledger,plan):
 for k,r in ledger['receipts'].items():
  if plan['domain_start_tick']<=r['begin_tick']<plan['domain_start_tick']+plan['cells']:
   e.need(k in plan['events'],'cannot cancel issued event')
   e.need(all(r[f]==plan['events'][k][f]for f in ('unit','phase','begin_tick','notice_tick','rows','curve_parameter_sha256')),'cannot alter issued event')
def _receipt(command,plan,at,token):
 _revalidate(command,plan,at)
 r=dict(**deepcopy(command['event']),issued=True,issued_tick=at,as_of_tick=at,source='explicit host-registered RESEARCH simulated shutdown instruction; not dispatcher authority',origin=plan['origin'],run_id=plan['run_id'],case_identity=plan['case_identity'],plan_sha256=plan['plan_sha256'],instruction_token=token,registered_at_tick=command['registered_at_tick'],formal=False,safety_release=False,actual_dispatch_authorized=False,DA_protection_changed=False,AGC_or_reserve_changed=False)
 r['receipt_hash']=e.digest(r);return r
