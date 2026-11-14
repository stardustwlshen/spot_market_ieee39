"""Same completed post-day reference only; never reissue native on unknown output."""
from pathlib import Path
from copy import deepcopy
from .engine import read,sha,need,verify,write
class HistoricalReference:
 def __init__(self,host,accepted,pins):self.host=host;self.accepted=deepcopy(accepted);self.pins=dict(pins);self.identity=(id(host),id(host.session),host.branch_id)
 def verify(self):
  need(self.identity==(id(self.host),id(self.host.session),self.host.branch_id),'参考宿主改变');self.host.session.verify_sources();verify(self.pins)
def select_or_execute(x,old,out):
 from post_reference import PostReferenceSession
 from reference_session import ReferenceOverlay,_KEY
 f=old/'pipeline/POST_D1_REFERENCE/ACCEPTED_REFERENCE.json'
 if not f.exists():
  need(not(old/'pipeline/POST_D1_REFERENCE/actual').exists(),'参考原生已有记录但未接受，禁止重新求解')
  from .historical_hour import HistoricalSelected
  record=x.host.hooks.post_scuc.accepted.get(24)
  if type(record)is HistoricalSelected:
   from historical_post_reference import HistoricalPostReferenceSession
   x.reference=HistoricalPostReferenceSession(x.host,out,source_output=old,research_reference_revision=True)
  else:x.reference=PostReferenceSession(x.host,out,research_reference_revision=True)
  x.reference.prepare();return x.reference.execute(execute_native=True),1
 accepted=read(f);verify(accepted['source_pins']);need(sha(accepted['path'])==accepted['sha256'],'历史参考结果改变')
 need(x.host.window==95 and x.host.pending is None,'参考只在W94完整接受后选择')
 # Historical reference pins identify old exact W94 input. Physical carry must match.
 oldcarry=read(old/'pipeline/dispatch/W94/EXACT_CARRY.json');current=read(x.host.cp)
 for k in ('thermal','thermal_age_h','storage','notice_values','cutoff_tick','delivery_tick'):need(oldcarry[k]==current[k],'参考边界与当前已选状态不同')
 owner=HistoricalReference(x.host,accepted,{**accepted['source_pins'],str(f):sha(f)});owner.verify();out.mkdir();(out/'actual').symlink_to(old/'pipeline/POST_D1_REFERENCE/actual',target_is_directory=True);write(out/'ACCEPTED_REFERENCE.json',accepted);write(out/'HISTORICAL_LINEAGE.json',dict(source=str(f),sha256=sha(f),new_native_calls=0,execution_attempt_id=x.execution_attempt_id,safety_release=False))
 return ReferenceOverlay(_KEY,owner),0
