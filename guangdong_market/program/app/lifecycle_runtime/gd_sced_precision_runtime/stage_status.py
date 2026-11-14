"""Transport/result shape gate only. Never numerical acceptance or retry authority."""
import json,math
class NativeStageFailure(ValueError):
 def __init__(self,report):
  self.report=report
  super().__init__('SCED_STAGE_FAILED '+json.dumps(report,sort_keys=True,allow_nan=False))
def require_optimal_sced(response,request,completion):
 r=response
 detail=dict(stage='SCED',native_status=r.get('native_status'),status=r.get('status'),error=r.get('error'),has_primal=r.get('has_primal'),dual_valid=r.get('dual_valid'),optimize_calls=r.get('optimize_calls'),transport_solve_entries=r.get('transport_solve_entries'),completion_solve_entries=completion.get('solve_entries'),no_retry=True,accepted=False,KKT_entered=False)
 def fail(reason):raise NativeStageFailure(dict(detail,reason=reason))
 if not(r.get('status')=='optimal' and r.get('native_status')=='Optimal' and r.get('has_primal')is True and r.get('dual_valid')is True):fail('native stage has no valid Optimal primal/dual result; original failure retained')
 for field,count in [('primal',len(request['variables'])),('dual',len(request['rows']))]:
  values=r.get(field)
  if not isinstance(values,list) or len(values)!=count or any(type(x)not in (int,float)or not math.isfinite(x)for x in values):fail('missing/malformed/nonfinite '+field+' witness; no mathematical audit attempted')
 if any(r.get(k)!=1 for k in ('optimize_calls','transport_solve_entries'))or completion.get('solve_entries')!=1:fail('unknown/repeated call count; no mathematical audit attempted')
 return None
