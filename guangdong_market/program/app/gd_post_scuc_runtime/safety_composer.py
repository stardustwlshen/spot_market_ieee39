"""Reapply an already registered future basepoint cap in full post-SCUC rebuilds.
Never a technical-capacity derating, AC remedy, source mint or safety release.
"""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import json,hashlib
read=lambda p:json.loads(Path(p).read_bytes())
from content_hash import sha
def need(ok,msg):
 if not ok:raise ValueError(msg)
class PreparedSafetyCaps:
 def __init__(self,host,prepared=None):
  from unified import RollingHost
  from tightening import TighteningAdapter
  need(isinstance(host,RollingHost),'registered host required')
  self.host=host;self.prepared=Path(prepared or host.pending).resolve();need(host.pending==self.prepared,'current pending candidate required')
  self.identity=(id(host.session),host.branch_id,host.window,str(host.cp),host.lid)
  self.adapter=TighteningAdapter(host);self.report_path=self.prepared.parent/'SAFETY_TIGHTENING.json';self.report=None
  self.pins={str(Path(__file__).resolve()):sha(__file__)}
  if self.report_path.exists():
   record=host.hooks.prepared[str(self.prepared)];need(record['artifact_pins'].get(str(self.report_path))==sha(self.report_path),'unregistered tightening report')
   self.report=read(self.report_path);self.pins[str(self.report_path)]=sha(self.report_path)
   need(self.report['branch_id']==host.branch_id and self.report['window']==host.window and Path(self.report['prepared']).resolve()==self.prepared,'report belongs to another candidate')
   need(self.report['constraint_semantics']=='redispatch_basepoint_only' and self.report['technical_capacity_unchanged'] is True and self.report['safety_release'] is False,'not basepoint-only research cap')
   path=Path(self.report['declaration_path']).resolve();need(sha(path)==self.report['declaration_sha256'],'declaration report mismatch');self.pins[str(path)]=sha(path)
  else:
   # A cap in the pending model may never disappear merely because a report is missing.
   names=read(self.prepared/'names.json')['constraints'];need(not any(x.startswith('research_safety_power_caps[')for x in names),'cap rows exist without registered report')
   need(not list(host.output.glob('safety_declarations/*.json')),'unresolved registered cap without candidate binding; explicit validity review required')
  host.session.registry['source_pins'].update(self.pins);self.verify()
 def verify(self):
  h=self.host;h.session.verify_sources();need(self.identity==(id(h.session),h.branch_id,h.window,str(h.cp),h.lid) and h.pending==self.prepared,'cap source clock/candidate advanced')
  need(h.hooks.carries.get(str(h.cp))==sha(h.cp),'cap live clock tampered')
  for path,value in self.pins.items():need(sha(path)==value,'cap/report/composer changed')
  record=h.hooks.prepared[str(self.prepared)]
  for path,value in record['artifact_pins'].items():need(sha(path)==value,'candidate artifact drift')
  if self.report is None:return None
  need(record['artifact_pins'].get(str(self.report_path))==self.pins[str(self.report_path)],'tightening report registration lost')
  p,d,begin=self.adapter.declaration(self.report['declaration_path'])
  need(sha(p)==self.report['declaration_sha256'],'declared cap changed')
  return d,begin
 def apply(self,model):
  checked=self.verify()
  if checked is None:return []
  d,begin=checked;rows=append_basepoint_rows(model,d,begin)
  need(rows==self.report['added_rows'],'rebuilt cap names/ticks/values differ from registered pending model')
  return rows
 def binding(self):
  self.verify();return dict(active=self.report is not None,source_pins=dict(self.pins),constraint_semantics='redispatch_basepoint_only',technical_capacity_unchanged=True,AGC_qualification_unchanged=True,publication=False,safety_release=False,AC_failure_resolved=False,native_calls=0)
def append_basepoint_rows(model,declaration,begin):
 """Pure model helper; no source authority. Live callers MUST use class above."""
 from pyomo.environ import ConstraintList
 need(not hasattr(model,'research_safety_power_caps'),'duplicate cap application')
 case=model._r23['inputs'];thermal=case['energy_inputs']['thermal']
 need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400/400 changed')
 need(datetime.fromisoformat(case['contract']['domain_start'])==begin,'cap applied at another model time')
 model.add_component('research_safety_power_caps',ConstraintList());rows=[]
 for x in declaration['constraints']:
  g=x['unit'];need(g in thermal,'unknown/unqualified thermal resource')
  start,end=datetime.fromisoformat(x['start']),datetime.fromisoformat(x['end']);need(begin<=start<end<=begin+timedelta(hours=4),'issued prefix/outside horizon')
  need((start-begin).total_seconds()%300==0 and(end-begin).total_seconds()%300==0,'not5min aligned')
  for t in range(int((start-begin).total_seconds()/300),int((end-begin).total_seconds()/300)):
   cap=x['maximum_power_mw'];need(thermal[g]['min_mw'][t]+10<=cap<=thermal[g]['max_mw'][t]-10,'cap violates original10MW headroom or relaxes physical bounds')
   row=model.research_safety_power_caps.add(model.power[g,t]<=cap);rows.append(dict(name=row.name,unit=g,tick=t,cap_mw=cap,kind='additional_upper_bound_only'))
 return rows
