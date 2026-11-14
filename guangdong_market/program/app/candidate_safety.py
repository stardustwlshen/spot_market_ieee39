"""Pre-handoff SafetySession extension. No historical receipt importer."""
from pathlib import Path
from copy import deepcopy
import json,hashlib

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
class CandidateSafetyMixin:
 def candidate_input(self,handle):
  gate=self.host.before_handoff_gate;p,a=gate.resolve(handle)
  self.verify()
  from ac_candidate import candidate_from_paths
  d,m,source=candidate_from_paths(self.host,p,a)
  gate.resolve(handle)
  return d,m,source
 def run_candidate_ac(self,handle,output,*,execute_ac=False):
  need(execute_ac is True and self.ac_calls<1,'explicit ac_once budget required')
  gate=self.host.before_handoff_gate;gate.resolve(handle)
  need(not hasattr(self,'_candidate_ac_receipt'),'candidate AC already attempted')
  d,m,source=self.candidate_input(handle)
  p,a=gate.resolve(handle);window=self.host.window
  out=Path(output);need(not out.exists(),'existing AC occurrence; no retry');out.mkdir(parents=True)
  pins=deepcopy(source)
  for n,x in [('PAYLOAD',d),('MAPPING',m),('SOURCE',pins),('STARTED',{'branch_id':self.host.branch_id,'window':self.host.window})]:
   (out/(n+'.json')).write_text(json.dumps(x,indent=2))
  self.ac_calls+=1;self._candidate_ac_receipt={'handle':handle,'status':'started_unknown','input':digest(m['ac_input'])}
  from ac_security import check_ac
  result=check_ac(m['ac_input'],max_iterations=60,tolerance_mva=1e-10)
  (out/'AC_RESULT.json').write_text(json.dumps(result,indent=2))
  gate.resolve(handle);self.verify()
  need(result['input_sha256']==digest(m['ac_input']),'AC returned different input')
  for item in pins['sources']:
   need(hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256'],'AC source drift')
  status='BLOCKED_OTHER_SAFETY_CHECKS' if result.get('ac_snapshot_safe') else 'BLOCKED_SYSTEM_SAFETY'
  self._candidate_ac_receipt=dict(handle=handle,status=status,input=digest(m['ac_input']),result=deepcopy(result),source=pins,payload=d,mapping=m,prepared=p,actual=a,window=window,raw_pins=deepcopy(gate._record[6]),attached=False)
  return self.candidate_status(handle)
 def candidate_status(self,handle):
  self.host.before_handoff_gate.resolve(handle)
  r=getattr(self,'_candidate_ac_receipt',None)
  need(r is not None and r['handle'] is handle,'no owned same-candidate AC receipt')
  return dict(status=r['status'],research_basepoint_safe=bool(r.get('result',{}).get('ac_snapshot_safe')),input_sha256=r['input'],safety_release=False,dispatch_authorized=False,formal=False)
 def candidate_loss_feedback_inputs(self,handle):
  """Owned failed/safe AC -> pure full-SCED feedback preparation, never registration.
  Caller must supply current full SCED, case, AGC and prefix context to existing
  build_unissued_feedback. Recleared full pair needs fresh host acceptance/AC.
  """
  self.candidate_status(handle);r=self._candidate_ac_receipt
  need('result' in r,'AC incomplete')
  return deepcopy(dict(mapping=r['mapping'],ac=r['result'],payload=r['payload'],source=r['source'],live_reclear_executed=False,PRICE_pending=True))

 def attach_candidate_ac_after_handoff(self,w):
  r=getattr(self,'_candidate_ac_receipt',None)
  if r is None or r.get('window')!=w:return False
  need('result' in r,'candidate AC incomplete')
  need(not r['attached'],'candidate AC already attached')
  self.verify();p,a=self.host.accepted_source(w)
  need(Path(p)==r['prepared'] and Path(a)==r['actual'],'handoff source substitution')
  need(self.host.window>w and self.host.pending!=p,'not actually handed off')
  for f,h in r['raw_pins'].items():need(hashlib.sha256(Path(f).read_bytes()).hexdigest()==h,'handed raw source drift')
  for item in r['source']['sources']:need(hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256'],'AC mapping source drift')
  from ac_candidate import candidate_from_paths
  d,m,_=candidate_from_paths(self.host,p,a)
  need(d==r['payload'] and m==r['mapping'],'effective handed-off AC semantics changed')
  self._record_ac(w,d,m,r['result'],'owned pre-handoff same-host candidate AC; no new call or JSON import')
  r['attached']=True
  return True
