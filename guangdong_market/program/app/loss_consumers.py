"""Exact owned occurrence extension; never restore registration from JSON."""
from copy import deepcopy
from loss_callback import owned,_OWNED,need
from loss_feedback import digest
_INSTALLED=set()
def install(program,event):
 if id(program)in _INSTALLED:return
 from gd_post_scuc_runtime import consumers
 oldchecked=program.engine.checked_pair;oldverify=event.verify_pair;oldcase=consumers.case_for;oldsemantics=consumers.semantics_for
 def checked(p,a):
  x=owned(p,a)
  if x is None:return oldchecked(p,a)
  x.verify_registered();return deepcopy(x.n),deepcopy(x.y)
 def case_for(p,a,default):
  x=owned(p,a)
  if x is None:return oldcase(p,a,default)
  x.verify_registered();return deepcopy(x.case) # gross physical load, NOT loss-added load
 def semantics_for(p,a):
  x=owned(p,a)
  if x is None:return oldsemantics(p,a)
  x.verify_registered()
  # Source post registry performs phase/raw/issued-receipt drift checks itself.
  z=oldsemantics(x.p,x.old)
  need(digest(z)==x.semantics_digest,'original post semantics changed')
  return deepcopy(z)
 def verify(q,u,eq,y,n):
  for x in _OWNED.values():
   if q==x.uq and u==x.u and eq==x.eq and y==x.y and n==x.n:
    x.verify_registered()
    return dict(SCUC=deepcopy(x.base_proof['SCUC']),SCED_KKT=x.audit['KKT'],loss_feedback=deepcopy(x.feedback),**deepcopy(x.certificate))
  return oldverify(q,u,eq,y,n)
 program.engine.checked_pair=checked;event.verify_pair=verify;consumers.case_for=case_for;consumers.semantics_for=semantics_for;_INSTALLED.add(id(program))

def proxy_for(p,a):
 x=owned(p,a)
 if x is None:return 0.
 x.verify_registered();return sum(x.feedback['node_loss_MW'].values())
